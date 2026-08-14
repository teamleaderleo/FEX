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

    # Keep the VMA implementation behind the syscall-handler interface. Thunk
    # registration only asks "which mapping generation owns T?".
    base = root / "FEXCore/include/FEXCore/HLE/SyscallHandler.h"
    repl(
        base,
        '''  virtual ExecutableRangeInfo QueryGuestExecutableRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) = 0;
  virtual std::optional<ExecutableFileSectionInfo> LookupExecutableFileSection(Core::InternalThreadState* Thread, uint64_t GuestAddr) = 0;''',
        '''  virtual ExecutableRangeInfo QueryGuestExecutableRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) = 0;
  virtual uint64_t QueryGuestMappingOwner(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) {
    return 0;
  }
  virtual std::optional<ExecutableFileSectionInfo> LookupExecutableFileSection(Core::InternalThreadState* Thread, uint64_t GuestAddr) = 0;''',
        'base owner query API',
    )

    sh = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/Syscalls.h"
    repl(
        sh,
        '''  FEXCore::HLE::ExecutableRangeInfo QueryGuestExecutableRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) override;''',
        '''  FEXCore::HLE::ExecutableRangeInfo QueryGuestExecutableRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) override;
  uint64_t QueryGuestMappingOwner(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) override;''',
        'Linux owner query declaration',
    )

    smc = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp"
    anchor = '''FEXCore::HLE::ExecutableRangeInfo SyscallHandler::QueryGuestExecutableRange(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) {
  auto lk = FEXCore::GuardSignalDeferringSection<std::shared_lock>(VMATracking.Mutex, Thread);
  auto ThreadObject = FEX::HLE::ThreadManager::GetStateObjectFromFEXCoreThread(Thread);

  auto Entry = VMATracking.FindVMAEntry(Address);
  if (Entry == VMATracking.VMAs.end() ||
      (!Entry->second.Prot.Executable && (!(ThreadObject->persona & READ_IMPLIES_EXEC) || !Entry->second.Prot.Readable))) {
    return {0, 0, false};
  }
  return {Entry->first, Entry->second.Length, Entry->second.Prot.Writable};
}
'''
    addition = anchor + '''
uint64_t SyscallHandler::QueryGuestMappingOwner(FEXCore::Core::InternalThreadState* Thread, uint64_t Address) {
  auto lk = FEXCore::GuardSignalDeferringSection<std::shared_lock>(VMATracking.Mutex, Thread);
  auto Entry = VMATracking.FindVMAEntry(Address);
  return Entry == VMATracking.VMAs.end() ? 0 : Entry->second.OwnerID;
}
'''
    repl(smc, anchor, addition, 'Linux owner query implementation')

    p = root / "Source/Tools/LinuxEmulation/Thunks.cpp"
    repl(
        p,
        '''  fextl::unordered_map<uintptr_t, fextl::vector<uintptr_t>> LinkedHostClaims;
  fextl::unordered_map<uintptr_t, uintptr_t> ActiveHostToGuest;''',
        '''  struct LinkedHostClaim {
    uintptr_t Target;
    uint64_t OwnerID;
  };

  fextl::unordered_map<uintptr_t, fextl::vector<LinkedHostClaim>> LinkedHostClaims;
  fextl::unordered_map<uintptr_t, uintptr_t> ActiveHostToGuest;''',
        'claim identity type',
    )
    repl(
        p,
        '''  struct HostClaimRetirementSnapshot {
    uintptr_t Host;
    fextl::vector<uintptr_t> Claims;
    uintptr_t Active;
  };''',
        '''  struct HostClaimRetirementSnapshot {
    uintptr_t Host;
    fextl::vector<LinkedHostClaim> Claims;
    uintptr_t Active;
  };''',
        'rollback claim snapshot identity',
    )

    repl(
        p,
        '''      Claims.erase(std::remove_if(Claims.begin(), Claims.end(), [&](uintptr_t Target) {
        const bool Retiring = Target >= Base && Target - Base < Length;
        if (Retiring) {
          fprintf(stderr, "DIAG_MULTI_DROP H=%#lx T=%#lx range=%#lx+%#lx\\n", Host, Target, Base, Length);
        }
        return Retiring;
      }), Claims.end());''',
        '''      Claims.erase(std::remove_if(Claims.begin(), Claims.end(), [&](const LinkedHostClaim& Claim) {
        const uintptr_t Target = Claim.Target;
        const bool Retiring = Target >= Base && Target - Base < Length;
        if (Retiring) {
          fprintf(stderr, "DIAG_MULTI_DROP H=%#lx T=%#lx owner=%#lx range=%#lx+%#lx\\n",
                  Host, Target, Claim.OwnerID, Base, Length);
        }
        return Retiring;
      }), Claims.end());''',
        'range retirement claim identity',
    )
    repl(
        p,
        '''        const uintptr_t NewActive = Claims.empty() ? 0 : Claims.front();''',
        '''        const uintptr_t NewActive = Claims.empty() ? 0 : Claims.front().Target;''',
        'promotion target from identified claim',
    )

    repl(
        p,
        '''      const bool Intersects = std::any_of(Claims.begin(), Claims.end(), [&](uintptr_t Target) {
        return Target >= Base && (Target - Base) < Length;
      });''',
        '''      const bool Intersects = std::any_of(Claims.begin(), Claims.end(), [&](const LinkedHostClaim& Claim) {
        return Claim.Target >= Base && (Claim.Target - Base) < Length;
      });''',
        'rollback snapshot identified claim scan',
    )

    old_link = r'''    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());
    bool FirstClaim = false;
    {
      std::lock_guard lk(ThunkHandler->ThunksMutex);
      auto& Claims = ThunkHandler->LinkedHostClaims[args->original_callee];
      if (std::find(Claims.begin(), Claims.end(), args->target_addr) == Claims.end()) {
        Claims.emplace_back(args->target_addr);
      }
      auto Active = ThunkHandler->ActiveHostToGuest.find(args->original_callee);
      if (Active == ThunkHandler->ActiveHostToGuest.end()) {
        ThunkHandler->ActiveHostToGuest[args->original_callee] = args->target_addr;
        FirstClaim = true;
      }
    }
    if (FirstClaim) {
      CTX->ActivateThunkTrampolineIRHandler(ThreadObject->Thread, args->original_callee, args->target_addr);
      fprintf(stderr, "DIAG_MULTI_ACTIVE H=%#lx T=%#lx\n", args->original_callee, args->target_addr);
    } else {
      fprintf(stderr, "DIAG_MULTI_STANDBY H=%#lx T=%#lx\n", args->original_callee, args->target_addr);
    }
'''
    new_link = r'''    auto ThunkHandler = reinterpret_cast<ThunkHandler_impl*>(FEX::HLE::_SyscallHandler->GetThunkHandler());
    const uint64_t OwnerID = FEX::HLE::_SyscallHandler->QueryGuestMappingOwner(ThreadObject->Thread, args->target_addr);
    bool FirstClaim = false;
    bool NewClaim = false;
    {
      std::lock_guard lk(ThunkHandler->ThunksMutex);
      auto& Claims = ThunkHandler->LinkedHostClaims[args->original_callee];
      const auto Existing = std::find_if(Claims.begin(), Claims.end(), [&](const ThunkHandler_impl::LinkedHostClaim& Claim) {
        return Claim.Target == args->target_addr && Claim.OwnerID == OwnerID;
      });
      if (Existing == Claims.end()) {
        Claims.emplace_back(ThunkHandler_impl::LinkedHostClaim {args->target_addr, OwnerID});
        NewClaim = true;
      }
      auto Active = ThunkHandler->ActiveHostToGuest.find(args->original_callee);
      if (Active == ThunkHandler->ActiveHostToGuest.end()) {
        ThunkHandler->ActiveHostToGuest[args->original_callee] = args->target_addr;
        FirstClaim = true;
      }
    }
    if (FirstClaim) {
      CTX->ActivateThunkTrampolineIRHandler(ThreadObject->Thread, args->original_callee, args->target_addr);
      fprintf(stderr, "DIAG_OWNER_CLAIM_ACTIVE H=%#lx T=%#lx owner=%#lx new=%d\n",
              args->original_callee, args->target_addr, OwnerID, NewClaim ? 1 : 0);
    } else {
      fprintf(stderr, "DIAG_OWNER_CLAIM_STANDBY H=%#lx T=%#lx owner=%#lx new=%d\n",
              args->original_callee, args->target_addr, OwnerID, NewClaim ? 1 : 0);
    }
'''
    repl(p, old_link, new_link, 'owner-aware LinkAddress registration')

    print('Added VMA owner identity to retained thunk claims')


if __name__ == '__main__':
    main()
