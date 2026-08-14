#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()
    smc = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp"
    text = smc.read_text()

    needle = '''uint64_t SyscallHandler::GuestMprotect(FEXCore::Core::InternalThreadState* Thread, void* addr, size_t len, int prot) {
  uint64_t Result {};
'''
    count = text.count(needle)
    if count != 1:
        raise SystemExit(f"mprotect active lease guard: expected one function prefix, found {count}")

    guard = '''

  // Removing execute permission from a guest range while an already-entered
  // callback owner generation is leased can invalidate its return path before
  // that lease drains. Reject only that destructive protection transition;
  // ordinary protection changes remain untouched.
  if (Thread && len && !(prot & PROT_EXEC)) {
    if (auto* Thunks = GetThunkHandler()) {
      const uintptr_t Base = reinterpret_cast<uintptr_t>(addr);
      const uintptr_t Length = FEXCore::AlignUp(len, FEXCore::Utils::FEX_PAGE_SIZE);
      if (Thunks->GuestRangeHasActiveExecutionLease(Thread, Base, Length)) {
        fprintf(stderr,
                "DIAG_CALLBACK_LEASE_MPROTECT_REJECT range=%#lx+%#lx prot=%#x errno=%d\\n",
                Base, Length, prot, EBUSY);
        return static_cast<uint64_t>(-EBUSY);
      }
    }
  }
'''
    smc.write_text(text.replace(needle, needle + guard, 1))
    print("Added EBUSY guard for execute-removing mprotect over an active callback OwnerID lease")


if __name__ == "__main__":
    main()
