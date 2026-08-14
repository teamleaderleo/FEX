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

    repl(
        smc,
        '''uint64_t SyscallHandler::GuestMremap(bool Is64Bit, FEXCore::Core::InternalThreadState* Thread, void* old_address, size_t old_size,
                                     size_t new_size, int flags, void* new_address) {''',
        '''#ifndef MREMAP_DONTUNMAP
#define MREMAP_DONTUNMAP 4
#endif
uint64_t SyscallHandler::GuestMremap(bool Is64Bit, FEXCore::Core::InternalThreadState* Thread, void* old_address, size_t old_size,
                                     size_t new_size, int flags, void* new_address) {''',
        'DONTUNMAP constant before GuestMremap',
    )

    old = '''  const bool FixedMove = (flags & MREMAP_FIXED) && new_address;
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
'''
    new = '''  const bool FixedMove = (flags & MREMAP_FIXED) && new_address;
  const bool DontUnmapMove = (flags & MREMAP_DONTUNMAP) && old_size;
  const bool SourceContentMoves = FixedMove || DontUnmapMove;
  if (Thread && SourceContentMoves) {
    if (auto* Thunks = GetThunkHandler()) {
      const auto OldLength = FEXCore::AlignUp(old_size, FEXCore::Utils::FEX_PAGE_SIZE);
      fprintf(stderr, "DIAG_MREMAP_PREPARE_SOURCE range=%#lx+%#lx dontunmap=%d fixed=%d\\n",
              reinterpret_cast<uintptr_t>(old_address), OldLength, DontUnmapMove ? 1 : 0, FixedMove ? 1 : 0);
      SourceRetirementToken =
        Thunks->PrepareGuestRangeRetirement(Thread, reinterpret_cast<uintptr_t>(old_address), OldLength);

      if (FixedMove) {
        const auto NewLength = FEXCore::AlignUp(new_size, FEXCore::Utils::FEX_PAGE_SIZE);
        fprintf(stderr, "DIAG_MREMAP_PREPARE_DEST range=%#lx+%#lx\\n",
                reinterpret_cast<uintptr_t>(new_address), NewLength);
        DestinationRetirementToken =
          Thunks->PrepareGuestRangeRetirement(Thread, reinterpret_cast<uintptr_t>(new_address), NewLength);
      }
    }
  }
'''
    repl(smc, old, new, 'DONTUNMAP source-content retirement')

    print('Extended remap lifetime transaction to retire MREMAP_DONTUNMAP source claims')


if __name__ == '__main__':
    main()
