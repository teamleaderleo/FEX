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
    h = root / "Source/Tools/LinuxEmulation/Thunks.h"
    cpp = root / "Source/Tools/LinuxEmulation/Thunks.cpp"
    smc = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp"

    once(
        h,
        "  virtual bool RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) = 0;\n",
        "  virtual bool GuestRangeHasActiveExecutionLease(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) = 0;\n"
        "  virtual bool RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) = 0;\n",
        "thunk interface lease query",
    )

    once(
        cpp,
        "  bool RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;\n",
        "  bool GuestRangeHasActiveExecutionLease(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;\n"
        "  bool RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;\n",
        "thunk implementation lease query declaration",
    )

    anchor = "uint64_t ThunkHandler_impl::PrepareGuestRangeRetirement(FEXCore::Core::InternalThreadState* Thread,\n"
    query_impl = '''bool ThunkHandler_impl::GuestRangeHasActiveExecutionLease(
  FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) {
  if (!Thread || !Length) {
    return false;
  }

  const uint64_t OwnerID = FEX::HLE::_SyscallHandler->QueryGuestMappingOwner(Thread, Base);
  if (!OwnerID) {
    return false;
  }

  std::lock_guard lk(ThunksMutex);
  auto It = CallbackOwnerGenerations.find(OwnerID);
  if (It == CallbackOwnerGenerations.end()) {
    return false;
  }

  const size_t Active = It->second->GetActive();
  if (Active != 0) {
    fprintf(stderr,
            "DIAG_CALLBACK_LEASE_REPLACE_BLOCK owner=%#lx active=%zu range=%#lx+%#lx\\n",
            OwnerID, Active, Base, Length);
    return true;
  }
  return false;
}

'''
    text = cpp.read_text()
    n = text.count(anchor)
    if n != 1:
        raise SystemExit(f"lease query implementation anchor: expected one, found {n}")
    cpp.write_text(text.replace(anchor, query_impl + anchor, 1))

    once(
        smc,
        '''  uint64_t ThunkRetirementToken {};
  if (Thread && Size && (flags & MAP_FIXED) && addr) {
    if (auto* Thunks = GetThunkHandler()) {
      fprintf(stderr, "DIAG_MAP_FIXED_PREPARE range=%#lx+%#lx\\n",
              reinterpret_cast<uintptr_t>(addr), Size);
      ThunkRetirementToken =
        Thunks->PrepareGuestRangeRetirement(Thread, reinterpret_cast<uintptr_t>(addr), Size);
    }
  }
''',
        '''  uint64_t ThunkRetirementToken {};
  if (Thread && Size && (flags & MAP_FIXED) && addr) {
    if (auto* Thunks = GetThunkHandler()) {
      if (Thunks->GuestRangeHasActiveExecutionLease(Thread, reinterpret_cast<uintptr_t>(addr), Size)) {
        fprintf(stderr, "DIAG_CALLBACK_LEASE_MAP_FIXED_REJECT range=%#lx+%#lx errno=%d\\n",
                reinterpret_cast<uintptr_t>(addr), Size, EBUSY);
        return reinterpret_cast<void*>(static_cast<intptr_t>(-EBUSY));
      }
      fprintf(stderr, "DIAG_MAP_FIXED_PREPARE range=%#lx+%#lx\\n",
              reinterpret_cast<uintptr_t>(addr), Size);
      ThunkRetirementToken =
        Thunks->PrepareGuestRangeRetirement(Thread, reinterpret_cast<uintptr_t>(addr), Size);
    }
  }
''',
        "MAP_FIXED active lease guard",
    )

    print("Added EBUSY guard for MAP_FIXED over an active callback OwnerID lease")


if __name__ == "__main__":
    main()
