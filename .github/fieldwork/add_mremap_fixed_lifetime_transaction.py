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

    old = '''uint64_t SyscallHandler::GuestMremap(bool Is64Bit, FEXCore::Core::InternalThreadState* Thread, void* old_address, size_t old_size,
                                     size_t new_size, int flags, void* new_address) {
  uint64_t Result {};

  {
    auto lk = FEXCore::GuardSignalDeferringSection(VMATracking.Mutex, Thread);
    if (Is64Bit) {
      Result = reinterpret_cast<uint64_t>(::mremap(old_address, old_size, new_size, flags, new_address));
      if (Result == -1) {
        return -errno;
      }
    } else {
      Result = reinterpret_cast<uint64_t>(Get32BitAllocator()->Mremap(old_address, old_size, new_size, flags, new_address));
      if (FEX::HLE::HasSyscallError(Result)) {
        return Result;
      }
    }
    TrackMremap(Thread, reinterpret_cast<uint64_t>(old_address), old_size, new_size, flags, Result);
  }

  InvalidateCodeRangeIfNecessaryOnRemap(Thread, reinterpret_cast<uint64_t>(old_address), Result, old_size, new_size);
  return Result;
}
'''

    new = '''uint64_t SyscallHandler::GuestMremap(bool Is64Bit, FEXCore::Core::InternalThreadState* Thread, void* old_address, size_t old_size,
                                     size_t new_size, int flags, void* new_address) {
  uint64_t Result {};
  std::optional<uint64_t> MremapFailureResult;
  uint64_t SourceRetirementToken {};
  uint64_t DestinationRetirementToken {};

  const bool FixedMove = (flags & MREMAP_FIXED) && new_address;
  if (Thread && FixedMove) {
    if (auto* Thunks = GetThunkHandler()) {
      const auto OldLength = FEXCore::AlignUp(old_size, FEXCore::Utils::FEX_PAGE_SIZE);
      const auto NewLength = FEXCore::AlignUp(new_size, FEXCore::Utils::FEX_PAGE_SIZE);
      fprintf(stderr, "DIAG_MREMAP_PREPARE_SOURCE range=%#lx+%#lx\\n",
              reinterpret_cast<uintptr_t>(old_address), OldLength);
      SourceRetirementToken =
        Thunks->PrepareGuestRangeRetirement(Thread, reinterpret_cast<uintptr_t>(old_address), OldLength);
      fprintf(stderr, "DIAG_MREMAP_PREPARE_DEST range=%#lx+%#lx\\n",
              reinterpret_cast<uintptr_t>(new_address), NewLength);
      DestinationRetirementToken =
        Thunks->PrepareGuestRangeRetirement(Thread, reinterpret_cast<uintptr_t>(new_address), NewLength);
    }
  }

  {
    auto lk = FEXCore::GuardSignalDeferringSection(VMATracking.Mutex, Thread);
    if (Is64Bit) {
      Result = reinterpret_cast<uint64_t>(::mremap(old_address, old_size, new_size, flags, new_address));
      if (Result == -1) {
        MremapFailureResult = static_cast<uint64_t>(-errno);
      }
    } else {
      Result = reinterpret_cast<uint64_t>(Get32BitAllocator()->Mremap(old_address, old_size, new_size, flags, new_address));
      if (FEX::HLE::HasSyscallError(Result)) {
        MremapFailureResult = Result;
      }
    }
    if (!MremapFailureResult) {
      TrackMremap(Thread, reinterpret_cast<uint64_t>(old_address), old_size, new_size, flags, Result);
    }
  }

  if (MremapFailureResult) {
    if (auto* Thunks = GetThunkHandler()) {
      // Reverse prepare order so overlapping diagnostic snapshots unwind
      // naturally if a deliberately-invalid fixed move was attempted.
      if (DestinationRetirementToken) {
        Thunks->RollbackGuestRangeRetirement(Thread, DestinationRetirementToken);
      }
      if (SourceRetirementToken) {
        Thunks->RollbackGuestRangeRetirement(Thread, SourceRetirementToken);
      }
    }
    fprintf(stderr, "DIAG_MREMAP_ROLLBACK result=%#lx source-token=%#lx dest-token=%#lx\\n",
            *MremapFailureResult, SourceRetirementToken, DestinationRetirementToken);
    return *MremapFailureResult;
  }

  if (auto* Thunks = GetThunkHandler()) {
    if (SourceRetirementToken) {
      Thunks->CommitGuestRangeRetirement(SourceRetirementToken);
    }
    if (DestinationRetirementToken) {
      Thunks->CommitGuestRangeRetirement(DestinationRetirementToken);
    }
  }

  InvalidateCodeRangeIfNecessaryOnRemap(Thread, reinterpret_cast<uint64_t>(old_address), Result, old_size, new_size);

  if (FixedMove) {
    // The generic remap invalidator handles the old source when addresses differ,
    // but MREMAP_FIXED also overwrites an unrelated destination mapping. That
    // destination translation must disappear before replacement bytes execute.
    const auto DestinationLength = FEXCore::AlignUp(new_size, FEXCore::Utils::FEX_PAGE_SIZE);
    fprintf(stderr, "DIAG_MREMAP_INVALIDATE_DEST range=%#lx+%#lx\\n",
            reinterpret_cast<uintptr_t>(new_address), DestinationLength);
    InvalidateCodeRangeIfNecessary(Thread, reinterpret_cast<uint64_t>(new_address), DestinationLength, false);
  }
  return Result;
}
'''

    repl(smc, old, new, 'GuestMremap transaction')
    print('Added MREMAP_FIXED source/destination retirement and destination code invalidation')


if __name__ == '__main__':
    main()
