#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    p = Path(sys.argv[1]).resolve() / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp"
    s = p.read_text()
    old = '''  if (Thread && Size) {
    if (auto* Thunks = GetThunkHandler()) {
      Thunks->RetireGuestRange(Thread, reinterpret_cast<uintptr_t>(addr), Size);
    }
  }
'''
    new = '''  const auto GuestMunmapAddress = reinterpret_cast<uintptr_t>(addr);
  const bool GuestMunmapBasicValid = length != 0 && (GuestMunmapAddress & (FEXCore::Utils::FEX_PAGE_SIZE - 1)) == 0;
  if (Thread && Size && GuestMunmapBasicValid) {
    if (auto* Thunks = GetThunkHandler()) {
      Thunks->RetireGuestRange(Thread, GuestMunmapAddress, Size);
    }
  }
'''
    if s.count(old) != 1:
        raise SystemExit(f"GuestMunmap retirement anchor count={s.count(old)}")
    p.write_text(s.replace(old, new, 1))
    print('Added basic munmap validity guard before pre-unmap bridge retirement')


if __name__ == '__main__':
    main()
