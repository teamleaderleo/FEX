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
    smc = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp"

    old = '''uint64_t SyscallHandler::GuestShmdt(bool Is64Bit, FEXCore::Core::InternalThreadState* Thread, const void* shmaddr) {
  uint64_t Result;
  uint64_t Length;
  bool PendingResourceDeletion;
  {
    auto lk = FEXCore::GuardSignalDeferringSection(VMATracking.Mutex, Thread);
    if (Is64Bit) {
      Result = ::shmdt(shmaddr);
      if (Result == -1) {
        return -errno;
      }
    } else {
      Result = Get32BitAllocator()->Shmdt(shmaddr);
      if (FEX::HLE::HasSyscallError(Result)) {
        return Result;
      }
    }

    Length = TrackShmdt(Thread, reinterpret_cast<uintptr_t>(shmaddr));
    PendingResourceDeletion = VMATracking.HasPendingResourceDeletions();
  }

  InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uintptr_t>(shmaddr), Length, PendingResourceDeletion);
  return Result;
}
'''

    new = '''uint64_t SyscallHandler::GuestShmdt(bool Is64Bit, FEXCore::Core::InternalThreadState* Thread, const void* shmaddr) {
  uint64_t Result {};
  uint64_t Length {};
  bool PendingResourceDeletion {};
  std::optional<uint64_t> ShmdtFailureResult;
  uint64_t ThunkRetirementToken {};

  // shmdt removes one attachment view while the backing SHM resource may remain
  // alive through other attachments. Discover the full attachment span before
  // the kernel removes the view, then retire H/callback dependencies outside
  // the VMA lock so lock ordering stays consistent with mmap/mremap retirement.
  {
    auto lk = FEXCore::GuardSignalDeferringSection<std::shared_lock>(VMATracking.Mutex, Thread);
    const auto Base = reinterpret_cast<uintptr_t>(shmaddr);
    auto Entry = VMATracking.FindVMAEntry(Base);
    if (Entry != VMATracking.VMAs.end() && Entry->first == Base && Entry->second.Offset == 0 && Entry->second.Resource &&
        Entry->second.Resource->Iterator->first.dev == VMATracking::SpecialDev::SHM) {
      Length = Entry->second.Resource->Length;
    }
  }

  if (Thread && Length) {
    if (auto* Thunks = GetThunkHandler()) {
      fprintf(stderr, "DIAG_SHMDT_PREPARE range=%#lx+%#lx\\n",
              reinterpret_cast<uintptr_t>(shmaddr), Length);
      ThunkRetirementToken =
        Thunks->PrepareGuestRangeRetirement(Thread, reinterpret_cast<uintptr_t>(shmaddr), Length);
    }
  }

  {
    auto lk = FEXCore::GuardSignalDeferringSection(VMATracking.Mutex, Thread);
    if (Is64Bit) {
      Result = ::shmdt(shmaddr);
      if (Result == -1) {
        ShmdtFailureResult = static_cast<uint64_t>(-errno);
      }
    } else {
      Result = Get32BitAllocator()->Shmdt(shmaddr);
      if (FEX::HLE::HasSyscallError(Result)) {
        ShmdtFailureResult = Result;
      }
    }

    if (!ShmdtFailureResult) {
      const uint64_t TrackedLength = TrackShmdt(Thread, reinterpret_cast<uintptr_t>(shmaddr));
      if (TrackedLength) {
        Length = TrackedLength;
      }
      PendingResourceDeletion = VMATracking.HasPendingResourceDeletions();
    }
  }

  if (ShmdtFailureResult) {
    if (ThunkRetirementToken) {
      GetThunkHandler()->RollbackGuestRangeRetirement(Thread, ThunkRetirementToken);
    }
    fprintf(stderr, "DIAG_SHMDT_ROLLBACK result=%#lx token=%#lx\\n",
            *ShmdtFailureResult, ThunkRetirementToken);
    return *ShmdtFailureResult;
  }

  if (ThunkRetirementToken) {
    GetThunkHandler()->CommitGuestRangeRetirement(ThunkRetirementToken);
    fprintf(stderr, "DIAG_SHMDT_COMMIT token=%#lx range=%#lx+%#lx\\n",
            ThunkRetirementToken, reinterpret_cast<uintptr_t>(shmaddr), Length);
  }

  InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uintptr_t>(shmaddr), Length, PendingResourceDeletion);
  return Result;
}
'''

    repl(smc, old, new, 'GuestShmdt view lifetime transaction')
    print('Added pre-shmdt attachment-view retirement with commit/rollback')


if __name__ == '__main__':
    main()
