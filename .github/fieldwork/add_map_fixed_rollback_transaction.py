#!/usr/bin/env python3
from pathlib import Path
import sys


def repl(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()

    # Extend the internal thunk-handler lifetime API with an opaque transaction
    # token. The snapshot itself stays inside ThunkHandler_impl so the syscall
    # layer cannot accidentally depend on claim-container details.
    th = root / "Source/Tools/LinuxEmulation/Thunks.h"
    repl(
        th,
        '  virtual void RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) = 0;',
        '''  virtual void RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) = 0;
  virtual uint64_t PrepareGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) = 0;
  virtual void CommitGuestRangeRetirement(uint64_t Token) = 0;
  virtual void RollbackGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uint64_t Token) = 0;''',
        'transaction API',
    )

    p = root / "Source/Tools/LinuxEmulation/Thunks.cpp"
    repl(
        p,
        '''  fextl::unordered_map<uintptr_t, fextl::vector<uintptr_t>> LinkedHostClaims;
  fextl::unordered_map<uintptr_t, uintptr_t> ActiveHostToGuest;''',
        '''  fextl::unordered_map<uintptr_t, fextl::vector<uintptr_t>> LinkedHostClaims;
  fextl::unordered_map<uintptr_t, uintptr_t> ActiveHostToGuest;

  struct HostClaimRetirementSnapshot {
    uintptr_t Host;
    fextl::vector<uintptr_t> Claims;
    uintptr_t Active;
  };

  struct CallbackRetirementSnapshot {
    GuestcallInfo Key;
    HostToGuestTrampolinePtr* Trampoline;
    TrampolineInstanceInfo Info;
  };

  struct GuestRangeRetirementSnapshot {
    fextl::vector<HostClaimRetirementSnapshot> Hosts;
    fextl::vector<CallbackRetirementSnapshot> Callbacks;
  };

  uint64_t NextGuestRangeRetirementToken {1};
  fextl::unordered_map<uint64_t, GuestRangeRetirementSnapshot> PendingGuestRangeRetirements;''',
        'transaction storage',
    )

    repl(
        p,
        '  void RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;',
        '''  void RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;
  uint64_t PrepareGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;
  void CommitGuestRangeRetirement(uint64_t Token) override;
  void RollbackGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uint64_t Token) override;''',
        'transaction method declarations',
    )

    anchor = 'static void IntegratedRevokedGuestCallback(void* GuestUnpacker, void* GuestTarget, void* ArgsRV) {'
    methods = r'''uint64_t ThunkHandler_impl::PrepareGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread,
                                                               uintptr_t Base, uintptr_t Length) {
  if (!Thread || !Length) {
    return 0;
  }

  GuestRangeRetirementSnapshot Snapshot;
  uint64_t Token {};
  {
    std::lock_guard lk(ThunksMutex);

    for (const auto& [Host, Claims] : LinkedHostClaims) {
      const bool Intersects = std::any_of(Claims.begin(), Claims.end(), [&](uintptr_t Target) {
        return Target >= Base && (Target - Base) < Length;
      });
      if (!Intersects) {
        continue;
      }

      const auto ActiveIt = ActiveHostToGuest.find(Host);
      const uintptr_t Active = ActiveIt == ActiveHostToGuest.end() ? 0 : ActiveIt->second;
      Snapshot.Hosts.push_back({Host, Claims, Active});
    }

    for (const auto& [Key, Trampoline] : GuestcallToHostTrampoline) {
      const bool UnpackerInRange = Key.GuestUnpacker >= Base && (Key.GuestUnpacker - Base) < Length;
      const bool TargetInRange = Key.GuestTarget >= Base && (Key.GuestTarget - Base) < Length;
      if (UnpackerInRange || TargetInRange) {
        Snapshot.Callbacks.push_back({Key, Trampoline, GetInstanceInfo(Trampoline)});
      }
    }

    if (Snapshot.Hosts.empty() && Snapshot.Callbacks.empty()) {
      return 0;
    }

    Token = NextGuestRangeRetirementToken++;
    LOGMAN_THROW_A_FMT(Token != 0, "Guest range retirement token wrapped to zero");
    PendingGuestRangeRetirements.emplace(Token, Snapshot);
  }

  fprintf(stderr, "DIAG_ROLLBACK_PREPARE token=%#lx range=%#lx+%#lx hosts=%zu callbacks=%zu\n",
          Token, Base, Length, Snapshot.Hosts.size(), Snapshot.Callbacks.size());
  RetireGuestRange(Thread, Base, Length);
  return Token;
}

void ThunkHandler_impl::CommitGuestRangeRetirement(uint64_t Token) {
  if (!Token) {
    return;
  }
  std::lock_guard lk(ThunksMutex);
  const auto Erased = PendingGuestRangeRetirements.erase(Token);
  fprintf(stderr, "DIAG_ROLLBACK_COMMIT token=%#lx snapshot=%zu\n", Token, Erased);
}

void ThunkHandler_impl::RollbackGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uint64_t Token) {
  if (!Thread || !Token) {
    return;
  }

  GuestRangeRetirementSnapshot Snapshot;
  {
    std::lock_guard lk(ThunksMutex);
    auto It = PendingGuestRangeRetirements.find(Token);
    LOGMAN_THROW_A_FMT(It != PendingGuestRangeRetirements.end(), "Missing guest range retirement token {:#x}", Token);
    Snapshot = std::move(It->second);
    PendingGuestRangeRetirements.erase(It);

    // Controlled diagnostic contract: no guest registration is allowed to race
    // between prepare and rollback. Production needs a transaction epoch/lock
    // rather than overwriting concurrent mutations this way.
    for (const auto& Host : Snapshot.Hosts) {
      if (LinkedHostClaims.contains(Host.Host)) {
        fprintf(stderr, "DIAG_ROLLBACK_CONFLICT H=%#lx existing-claims=%zu\n",
                Host.Host, LinkedHostClaims[Host.Host].size());
      }
      LinkedHostClaims[Host.Host] = Host.Claims;
      if (Host.Active) {
        ActiveHostToGuest[Host.Host] = Host.Active;
      } else {
        ActiveHostToGuest.erase(Host.Host);
      }
    }

    for (const auto& Callback : Snapshot.Callbacks) {
      GetInstanceInfo(Callback.Trampoline) = Callback.Info;
      GuestcallToHostTrampoline[Callback.Key] = Callback.Trampoline;
    }
  }

  auto CTX = static_cast<FEXCore::Context::Context*>(Thread->CTX);
  for (const auto& Host : Snapshot.Hosts) {
    if (Host.Active) {
      CTX->ActivateThunkTrampolineIRHandler(Thread, Host.Host, Host.Active);
      fprintf(stderr, "DIAG_ROLLBACK_RESTORE H=%#lx T=%#lx claims=%zu\n",
              Host.Host, Host.Active, Host.Claims.size());
    } else {
      CTX->RetireThunkTrampolineIRHandler(Thread, Host.Host);
    }
  }
  for (const auto& Callback : Snapshot.Callbacks) {
    fprintf(stderr, "DIAG_ROLLBACK_CALLBACK trampoline=%p unpacker=%#lx target=%#lx\n",
            Callback.Trampoline, Callback.Key.GuestUnpacker, Callback.Key.GuestTarget);
  }
  fprintf(stderr, "DIAG_ROLLBACK_DONE token=%#lx hosts=%zu callbacks=%zu\n",
          Token, Snapshot.Hosts.size(), Snapshot.Callbacks.size());
}

'''
    text = p.read_text()
    count = text.count(anchor)
    if count != 1:
        raise SystemExit(f"transaction definition anchor: expected one match, found {count}")
    p.write_text(text.replace(anchor, methods + anchor, 1))

    # Convert the causal pre-MAP_FIXED hook to prepare a transaction token.
    smc = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp"
    repl(
        smc,
        r'''  // Diagnostic lifetime transaction: MAP_FIXED destroys any previous mapping
  // generation covering [addr, addr + Size). Retire dependent thunk claims
  // while the old generation is still present, before the host mmap can replace
  // it. This intentionally uses the current range-based claim index to test the
  // ordering/hook; production ownership should use a non-reusable VMA owner ID.
  //
  // Experimental limitation: this probe does not restore retired claims if the
  // subsequent host mmap fails. The controlled test uses a valid aligned
  // MAP_FIXED replacement, so failure rollback is left to the owner-token design.
  if (Thread && Size && (flags & MAP_FIXED) && addr) {
    if (auto* Thunks = GetThunkHandler()) {
      fprintf(stderr, "DIAG_MAP_FIXED_PREPARE range=%#lx+%#lx\n",
              reinterpret_cast<uintptr_t>(addr), Size);
      Thunks->RetireGuestRange(Thread, reinterpret_cast<uintptr_t>(addr), Size);
    }
  }
''',
        r'''  uint64_t ThunkRetirementToken {};
  if (Thread && Size && (flags & MAP_FIXED) && addr) {
    if (auto* Thunks = GetThunkHandler()) {
      fprintf(stderr, "DIAG_MAP_FIXED_PREPARE range=%#lx+%#lx\n",
              reinterpret_cast<uintptr_t>(addr), Size);
      ThunkRetirementToken =
        Thunks->PrepareGuestRangeRetirement(Thread, reinterpret_cast<uintptr_t>(addr), Size);
    }
  }
''',
        'transactional MAP_FIXED prepare',
    )

    repl(
        smc,
        '''  std::optional<FEXCore::ExecutableFileSectionInfo> CachedSection;
  bool PendingResourceDeletion;''',
        '''  std::optional<FEXCore::ExecutableFileSectionInfo> CachedSection;
  bool PendingResourceDeletion;
  std::optional<uint64_t> MmapFailureResult;''',
        'deferred mmap failure',
    )

    repl(
        smc,
        '''    bool Map32Bit = !Is64Bit || (flags & FEX::HLE::X86_64_MAP_32BIT);
    if (Map32Bit) {
      Result = (uint64_t)Get32BitAllocator()->Mmap((void*)addr, length, prot, flags, fd, offset);
      if (FEX::HLE::HasSyscallError(Result)) {
        return reinterpret_cast<void*>(Result);
      }
      LOGMAN_THROW_A_FMT(Is64Bit || (Result >> 32) == 0 || (Result >> 32) == 0xFFFFFFFF, "values must fit to 32 bits");
    } else {
      Result = reinterpret_cast<uint64_t>(::mmap(reinterpret_cast<void*>(addr), length, prot, flags, fd, offset));
      if (Result == ~0ULL) {
        return reinterpret_cast<void*>(-errno);
      }
    }

    LateMetadata = TrackMmap(Thread, Result, length, prot, flags, fd, offset, CachedSection);
    PendingResourceDeletion = VMATracking.HasPendingResourceDeletions();''',
        '''    bool Map32Bit = !Is64Bit || (flags & FEX::HLE::X86_64_MAP_32BIT);
    if (Map32Bit) {
      Result = (uint64_t)Get32BitAllocator()->Mmap((void*)addr, length, prot, flags, fd, offset);
      if (FEX::HLE::HasSyscallError(Result)) {
        MmapFailureResult = Result;
      } else {
        LOGMAN_THROW_A_FMT(Is64Bit || (Result >> 32) == 0 || (Result >> 32) == 0xFFFFFFFF, "values must fit to 32 bits");
      }
    } else {
      Result = reinterpret_cast<uint64_t>(::mmap(reinterpret_cast<void*>(addr), length, prot, flags, fd, offset));
      if (Result == ~0ULL) {
        MmapFailureResult = static_cast<uint64_t>(-errno);
      }
    }

    if (!MmapFailureResult) {
      LateMetadata = TrackMmap(Thread, Result, length, prot, flags, fd, offset, CachedSection);
      PendingResourceDeletion = VMATracking.HasPendingResourceDeletions();
    }''',
        'defer mmap return until VMA lock released',
    )

    repl(
        smc,
        '''  }

  InvalidateCodeRangeIfNecessary(Thread, Result, Size, PendingResourceDeletion);''',
        '''  }

  if (MmapFailureResult) {
    if (ThunkRetirementToken) {
      GetThunkHandler()->RollbackGuestRangeRetirement(Thread, ThunkRetirementToken);
    }
    return reinterpret_cast<void*>(*MmapFailureResult);
  }

  if (ThunkRetirementToken) {
    GetThunkHandler()->CommitGuestRangeRetirement(ThunkRetirementToken);
  }

  InvalidateCodeRangeIfNecessary(Thread, Result, Size, PendingResourceDeletion);''',
        'MAP_FIXED commit or rollback after VMA lock',
    )

    print('Added serial guest-range retirement prepare/commit/rollback transaction')


if __name__ == '__main__':
    main()
