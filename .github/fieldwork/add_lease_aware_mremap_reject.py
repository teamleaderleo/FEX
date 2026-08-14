#!/usr/bin/env python3
from pathlib import Path
import sys


def once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor, found {n}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()
    smc = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp"

    once(
        smc,
        '''uint64_t SyscallHandler::GuestMremap(bool Is64Bit, FEXCore::Core::InternalThreadState* Thread, void* old_address, size_t old_size,
                                     size_t new_size, int flags, void* new_address) {
  uint64_t Result {};

  {
''',
        '''uint64_t SyscallHandler::GuestMremap(bool Is64Bit, FEXCore::Core::InternalThreadState* Thread, void* old_address, size_t old_size,
                                     size_t new_size, int flags, void* new_address) {
  uint64_t Result {};

#ifndef MREMAP_DONTUNMAP
#define MREMAP_DONTUNMAP 4
#endif
  // A successful mremap without DONTUNMAP may remove or move the source
  // mapping before an already-entered callback lease releases. Keep the same
  // temporary-EBUSY policy used by destructive MAP_FIXED while the source
  // mapping generation is actively leased.
  if (Thread && old_size && !(flags & MREMAP_DONTUNMAP)) {
    if (auto* Thunks = GetThunkHandler()) {
      const uintptr_t Base = reinterpret_cast<uintptr_t>(old_address);
      const uintptr_t Length = FEXCore::AlignUp(old_size, FEXCore::Utils::FEX_PAGE_SIZE);
      if (Thunks->GuestRangeHasActiveExecutionLease(Thread, Base, Length)) {
        fprintf(stderr,
                "DIAG_CALLBACK_LEASE_MREMAP_REJECT range=%#lx+%#lx flags=%#x errno=%d\\n",
                Base, Length, flags, EBUSY);
        return static_cast<uint64_t>(-EBUSY);
      }
    }
  }

  {
''',
        "mremap active lease guard",
    )

    print("Added EBUSY guard for destructive mremap over an active callback OwnerID lease")


if __name__ == "__main__":
    main()
