#!/usr/bin/env python3
from pathlib import Path
import sys


def once(path: Path, old: str, new: str, label: str) -> None:
    s = path.read_text()
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor in {path}, found {n}")
    path.write_text(s.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()
    th = root / "Source/Tools/LinuxEmulation/Thunks.h"
    cpp = root / "Source/Tools/LinuxEmulation/Thunks.cpp"
    smc = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp"

    once(
        th,
        "  virtual void RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) = 0;",
        "  virtual void BeginGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) = 0;\n"
        "  virtual void CommitGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) = 0;\n"
        "  virtual void RollbackGuestRangeRetirement(uintptr_t Base, uintptr_t Length) = 0;",
        "transactional thunk retirement interface",
    )

    once(
        cpp,
        '''  void RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;
''',
        '''  void BeginGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;
  void CommitGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;
  void RollbackGuestRangeRetirement(uintptr_t Base, uintptr_t Length) override;
''',
        "transactional thunk retirement declarations",
    )

    once(
        cpp,
        '''  void DrainAndRevoke() {
    std::unique_lock lk(Mutex);
    CV.wait(lk, [this]() { return Active == 0; });
    Status = State::Revoked;
  }
''',
        '''  void WaitForDrain() {
    std::unique_lock lk(Mutex);
    CV.wait(lk, [this]() { return Active == 0; });
  }

  void CommitRevoke() {
    std::lock_guard lk(Mutex);
    Status = State::Revoked;
    DrainRequests = 0;
  }

  void RollbackDrain() {
    std::lock_guard lk(Mutex);
    LOGMAN_THROW_A_FMT(DrainRequests != 0, "Callback descriptor drain-request underflow");
    --DrainRequests;
    if (DrainRequests == 0 && Status == State::Draining) {
      Status = State::Live;
    }
  }
''',
        "separate wait commit rollback",
    )

    once(
        cpp,
        '''  void BeginDrain() {
    std::lock_guard lk(Mutex);
    if (Status == State::Live) {
      Status = State::Draining;
    }
  }
''',
        '''  void BeginDrain() {
    std::lock_guard lk(Mutex);
    if (Status == State::Revoked) {
      return;
    }
    ++DrainRequests;
    Status = State::Draining;
  }
''',
        "drain request counting",
    )

    once(
        cpp,
        '''  size_t Active {};
  const uintptr_t GuestUnpacker;
''',
        '''  size_t Active {};
  size_t DrainRequests {};
  const uintptr_t GuestUnpacker;
''',
        "descriptor drain request field",
    )

    # Add a range registry under the same global thunk lock. New callback
    # descriptors created while a range is draining are born Draining and cannot
    # acquire execution before the munmap transaction commits or rolls back.
    marker = '''  fextl::unordered_map<GuestcallInfo, HostToGuestTrampolinePtr*, GuestcallInfoHash> GuestcallToHostTrampoline;
'''
    repl = marker + '''
  struct DrainingGuestRange {
    uintptr_t Base;
    uintptr_t Length;

    bool Contains(uintptr_t Address) const {
      return Address >= Base && (Address - Base) < Length;
    }
  };
  fextl::vector<DrainingGuestRange> DrainingGuestRanges;
'''
    once(cpp, marker, repl, "draining guest range registry")

    start = cpp.read_text().find("void ThunkHandler_impl::RetireGuestRange(")
    end_marker = "\nFEX_DEFAULT_VISIBILITY HostToGuestTrampolinePtr*\nMakeHostTrampolineForGuestFunction"
    text = cpp.read_text()
    end = text.find(end_marker, start)
    if start < 0 or end < 0:
      raise SystemExit("retirement function block not found")

    transaction_impl = r'''void ThunkHandler_impl::BeginGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) {
  if (!Thread || !Length) return;

  fextl::vector<GuestCallbackDescriptor*> CallbackDescriptorsToDrain;
  {
    std::lock_guard lk(ThunksMutex);
    DrainingGuestRanges.emplace_back(DrainingGuestRange {Base, Length});
    fprintf(stderr, "DIAG_CALLBACK_TX_BEGIN range=%#lx+%#lx\n", Base, Length);

    for (auto& [Guestcall, Trampoline] : GuestcallToHostTrampoline) {
      const bool UnpackerInRange = Guestcall.GuestUnpacker >= Base && (Guestcall.GuestUnpacker - Base) < Length;
      const bool TargetInRange = Guestcall.GuestTarget >= Base && (Guestcall.GuestTarget - Base) < Length;
      if (!UnpackerInRange && !TargetInRange) continue;

      auto* Descriptor = reinterpret_cast<GuestCallbackDescriptor*>(GetInstanceInfo(Trampoline).GuestUnpacker);
      LOGMAN_THROW_A_FMT(Descriptor != nullptr, "Beginning callback retirement without descriptor");
      Descriptor->BeginDrain();
      CallbackDescriptorsToDrain.emplace_back(Descriptor);
      fprintf(stderr,
              "DIAG_CALLBACK_TX_DRAIN_BEGIN trampoline=%p descriptor=%p unpacker=%#lx target=%#lx active=%zu range=%#lx+%#lx\n",
              Trampoline, Descriptor, Guestcall.GuestUnpacker, Guestcall.GuestTarget,
              Descriptor->GetActive(), Base, Length);
    }
  }

  // Never wait while holding ThunksMutex. An already-active guest callback may
  // itself invoke another thunk before returning.
  for (auto* Descriptor : CallbackDescriptorsToDrain) {
    fprintf(stderr, "DIAG_CALLBACK_TX_DRAIN_WAIT descriptor=%p active=%zu\n",
            Descriptor, Descriptor->GetActive());
    Descriptor->WaitForDrain();
    fprintf(stderr, "DIAG_CALLBACK_TX_DRAIN_READY descriptor=%p active=%zu\n",
            Descriptor, Descriptor->GetActive());
  }
}

void ThunkHandler_impl::CommitGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) {
  if (!Thread || !Length) return;

  struct Transition {
    uintptr_t Host;
    uintptr_t OldTarget;
    uintptr_t NewTarget;
  };
  fextl::vector<Transition> Transitions;

  {
    std::lock_guard lk(ThunksMutex);

    // Commit every descriptor currently depending on the unmapped range,
    // including descriptors created after Begin while the range was draining.
    for (auto It = GuestcallToHostTrampoline.begin(); It != GuestcallToHostTrampoline.end();) {
      const auto Unpacker = It->first.GuestUnpacker;
      const auto Target = It->first.GuestTarget;
      const bool UnpackerInRange = Unpacker >= Base && (Unpacker - Base) < Length;
      const bool TargetInRange = Target >= Base && (Target - Base) < Length;
      if (!UnpackerInRange && !TargetInRange) {
        ++It;
        continue;
      }

      auto* Trampoline = It->second;
      auto* Descriptor = reinterpret_cast<GuestCallbackDescriptor*>(GetInstanceInfo(Trampoline).GuestUnpacker);
      LOGMAN_THROW_A_FMT(Descriptor != nullptr, "Committing callback retirement without descriptor");
      Descriptor->CommitRevoke();
      fprintf(stderr,
              "DIAG_CALLBACK_TX_COMMIT trampoline=%p descriptor=%p unpacker=%#lx target=%#lx range=%#lx+%#lx\n",
              Trampoline, Descriptor, Unpacker, Target, Base, Length);
      It = GuestcallToHostTrampoline.erase(It);
    }

    // Dynamic H->T state is retired only after host munmap succeeds. This is
    // transaction-safe but, by itself, does not close an already-selected H->T
    // execution window. The staged design pairs this callback transaction with
    // resident generated PFN bridge targets.
    for (auto it = LinkedHostClaims.begin(); it != LinkedHostClaims.end();) {
      const uintptr_t Host = it->first;
      auto& Claims = it->second;
      const auto ActiveIt = ActiveHostToGuest.find(Host);
      const uintptr_t OldActive = ActiveIt == ActiveHostToGuest.end() ? 0 : ActiveIt->second;
      const bool ActiveRetires = OldActive >= Base && OldActive - Base < Length;

      Claims.erase(std::remove_if(Claims.begin(), Claims.end(), [&](uintptr_t Target) {
        return Target >= Base && Target - Base < Length;
      }), Claims.end());

      if (ActiveRetires) {
        const uintptr_t NewActive = Claims.empty() ? 0 : Claims.front();
        Transitions.push_back({Host, OldActive, NewActive});
        if (NewActive) ActiveHostToGuest[Host] = NewActive;
        else ActiveHostToGuest.erase(Host);
      }

      if (Claims.empty()) it = LinkedHostClaims.erase(it);
      else ++it;
    }

    auto Range = std::find_if(DrainingGuestRanges.begin(), DrainingGuestRanges.end(), [&](const DrainingGuestRange& R) {
      return R.Base == Base && R.Length == Length;
    });
    LOGMAN_THROW_A_FMT(Range != DrainingGuestRanges.end(), "Committing unknown guest-range retirement");
    DrainingGuestRanges.erase(Range);
  }

  auto CTX = static_cast<FEXCore::Context::Context*>(Thread->CTX);
  for (auto& Transition : Transitions) {
    CTX->RetireThunkTrampolineIRHandler(Thread, Transition.Host);
    if (Transition.NewTarget) {
      CTX->ActivateThunkTrampolineIRHandler(Thread, Transition.Host, Transition.NewTarget);
    }
  }
  fprintf(stderr, "DIAG_CALLBACK_TX_COMMIT_RANGE range=%#lx+%#lx\n", Base, Length);
}

void ThunkHandler_impl::RollbackGuestRangeRetirement(uintptr_t Base, uintptr_t Length) {
  if (!Length) return;

  std::lock_guard lk(ThunksMutex);
  for (auto& [Guestcall, Trampoline] : GuestcallToHostTrampoline) {
    const bool UnpackerInRange = Guestcall.GuestUnpacker >= Base && (Guestcall.GuestUnpacker - Base) < Length;
    const bool TargetInRange = Guestcall.GuestTarget >= Base && (Guestcall.GuestTarget - Base) < Length;
    if (!UnpackerInRange && !TargetInRange) continue;

    auto* Descriptor = reinterpret_cast<GuestCallbackDescriptor*>(GetInstanceInfo(Trampoline).GuestUnpacker);
    LOGMAN_THROW_A_FMT(Descriptor != nullptr, "Rolling back callback retirement without descriptor");
    Descriptor->RollbackDrain();
    fprintf(stderr,
            "DIAG_CALLBACK_TX_ROLLBACK descriptor=%p unpacker=%#lx target=%#lx state=%u range=%#lx+%#lx\n",
            Descriptor, Guestcall.GuestUnpacker, Guestcall.GuestTarget,
            static_cast<unsigned>(Descriptor->GetState()), Base, Length);
  }

  auto Range = std::find_if(DrainingGuestRanges.begin(), DrainingGuestRanges.end(), [&](const DrainingGuestRange& R) {
    return R.Base == Base && R.Length == Length;
  });
  LOGMAN_THROW_A_FMT(Range != DrainingGuestRanges.end(), "Rolling back unknown guest-range retirement");
  DrainingGuestRanges.erase(Range);
  fprintf(stderr, "DIAG_CALLBACK_TX_ROLLBACK_RANGE range=%#lx+%#lx\n", Base, Length);
}
'''
    cpp.write_text(text[:start] + transaction_impl + text[end:])

    # A callback published while a range is draining must not become a fresh
    # Live escape hatch between the initial scan and the host munmap.
    once(
        cpp,
        '''  auto* Descriptor = new GuestCallbackDescriptor {GuestUnpacker, GuestTarget};
  GetInstanceInfo(HostTrampoline) = TrampolineInstanceInfo {
''',
        '''  auto* Descriptor = new GuestCallbackDescriptor {GuestUnpacker, GuestTarget};
  for (const auto& Range : ThunkHandler->DrainingGuestRanges) {
    if (Range.Contains(GuestUnpacker) || Range.Contains(GuestTarget)) {
      Descriptor->BeginDrain();
      fprintf(stderr,
              "DIAG_CALLBACK_TX_CREATE_DRAINING descriptor=%p unpacker=%#lx target=%#lx range=%#lx+%#lx\\n",
              Descriptor, GuestUnpacker, GuestTarget, Range.Base, Range.Length);
    }
  }
  GetInstanceInfo(HostTrampoline) = TrampolineInstanceInfo {
''',
        "new descriptor inherits draining range",
    )

    # Replace the eager pre-munmap hook and stock early-return shape with a
    # three-phase transaction. Rollback happens after releasing VMATracking.Mutex
    # to avoid introducing a VMATracking->Thunks lock-order inversion.
    text = smc.read_text()
    fn_start = text.find("uint64_t SyscallHandler::GuestMunmap(")
    fn_end = text.find("\nuint64_t SyscallHandler::GuestMremap(", fn_start)
    if fn_start < 0 or fn_end < 0:
        raise SystemExit("GuestMunmap function boundaries not found")

    new_fn = r'''uint64_t SyscallHandler::GuestMunmap(bool Is64Bit, FEXCore::Core::InternalThreadState* Thread, void* addr, uint64_t length) {
  LOGMAN_THROW_A_FMT(Is64Bit || (reinterpret_cast<uintptr_t>(addr) >> 32) == 0, "values must fit to 32 bits: {}", fmt::ptr(addr));
  LOGMAN_THROW_A_FMT(Is64Bit || (length >> 32) == 0, "values must fit to 32 bits");

  uint64_t Result;
  uint64_t Size = FEXCore::AlignUp(length, FEXCore::Utils::FEX_PAGE_SIZE);
  bool PendingResourceDeletion {};
  bool MunmapFailed = false;

  auto* Thunks = (Thread && Size) ? GetThunkHandler() : nullptr;
  if (Thunks) {
    Thunks->BeginGuestRangeRetirement(Thread, reinterpret_cast<uintptr_t>(addr), Size);
  }

  {
    // Do not hold VMATracking.Mutex while waiting for callback execution drain.
    // BeginGuestRangeRetirement has already completed its wait before this lock.
    auto lk = FEXCore::GuardSignalDeferringSectionWithFallback(VMATracking.Mutex, Thread);

    if (reinterpret_cast<uintptr_t>(addr) < 0x1'0000'0000ULL) {
      Result = Get32BitAllocator()->Munmap(addr, length);
      MunmapFailed = FEX::HLE::HasSyscallError(Result);
    } else {
      Result = ::munmap(addr, length);
      if (Result == -1) {
        Result = -errno;
        MunmapFailed = true;
      }
    }

    if (!MunmapFailed) {
      TrackMunmap(Thread, addr, length);
      PendingResourceDeletion = VMATracking.HasPendingResourceDeletions();
    }
  }

  if (MunmapFailed) {
    if (Thunks) {
      Thunks->RollbackGuestRangeRetirement(reinterpret_cast<uintptr_t>(addr), Size);
    }
    return Result;
  }

  if (Thunks) {
    Thunks->CommitGuestRangeRetirement(Thread, reinterpret_cast<uintptr_t>(addr), Size);
  }

  InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uint64_t>(addr), Size, PendingResourceDeletion);

  if (length) {
    auto CodeInvalidationlk = FEXCore::GuardSignalDeferringSectionWithFallback(CTX->GetCodeInvalidationMutex(), Thread);
    CTX->RemoveForceTSOInformation(reinterpret_cast<uint64_t>(addr), length);
  }

  return Result;
}
'''
    smc.write_text(text[:fn_start] + new_fn + text[fn_end:])

    print("Added transactional callback retirement: BeginDrain -> munmap -> Commit/Rollback")


if __name__ == "__main__":
    main()
