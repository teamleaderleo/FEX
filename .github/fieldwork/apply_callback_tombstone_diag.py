#!/usr/bin/env python3
from pathlib import Path
import sys


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()

    thunks_h = root / "Source/Tools/LinuxEmulation/Thunks.h"
    text = thunks_h.read_text()
    marker = "namespace FEX::HLE {\n"
    if text.count(marker) != 1:
        raise SystemExit("Thunks.h namespace marker missing")
    text = text.replace(marker, "namespace FEXCore::Core { struct InternalThreadState; }\n\n" + marker, 1)
    old = "  virtual void AppendThunkDefinitions(std::span<const FEXCore::IR::ThunkDefinition> Definitions) = 0;"
    new = """  virtual void AppendThunkDefinitions(std::span<const FEXCore::IR::ThunkDefinition> Definitions) = 0;
  virtual void RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) = 0;"""
    if text.count(old) != 1:
        raise SystemExit("ThunkHandler interface marker missing")
    thunks_h.write_text(text.replace(old, new, 1))

    thunks_cpp = root / "Source/Tools/LinuxEmulation/Thunks.cpp"
    text = thunks_cpp.read_text()
    if "#include <cstdlib>" not in text:
        text = text.replace("#include <cstdint>\n", "#include <cstdint>\n#include <cstdlib>\n", 1)

    old = "  void AppendThunkDefinitions(std::span<const FEXCore::IR::ThunkDefinition> Definitions) override {"
    new = """  void RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) override;

  void AppendThunkDefinitions(std::span<const FEXCore::IR::ThunkDefinition> Definitions) override {"""
    if text.count(old) != 1:
        raise SystemExit("ThunkHandler method marker missing")
    text = text.replace(old, new, 1)

    old = """FEX_DEFAULT_VISIBILITY HostToGuestTrampolinePtr*
MakeHostTrampolineForGuestFunction"""
    new = r'''static void RevokedGuestCallback(void* GuestUnpacker, void* GuestTarget, void* ArgsRV) {
  (void)GuestUnpacker;
  (void)GuestTarget;
  (void)ArgsRV;
  fprintf(stderr, "DIAG_CALLBACK_REVOKED invoked\n");
  std::_Exit(113);
}

void ThunkHandler_impl::RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) {
  (void)Thread;
  if (!Length) {
    return;
  }

  std::lock_guard lk(ThunksMutex);
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
    auto& Info = GetInstanceInfo(Trampoline);
    fprintf(stderr,
            "DIAG_CALLBACK_TOMBSTONE trampoline=%p unpacker=%#lx target=%#lx range=%#lx+%#lx\n",
            Trampoline, Unpacker, Target, Base, Length);

    // Keep the escaped host trampoline pointer executable, but make its next
    // invocation terminate through a controlled FEX-owned host path instead of
    // entering retired guest code. Erase the cache key as well so same-address
    // guest reload cannot ABA-reuse this tombstoned instance.
    Info.CallCallback = reinterpret_cast<uintptr_t>(&RevokedGuestCallback);
    Info.GuestUnpacker = 0;
    Info.GuestTarget = 0;
    It = GuestcallToHostTrampoline.erase(It);
  }
}

FEX_DEFAULT_VISIBILITY HostToGuestTrampolinePtr*
MakeHostTrampolineForGuestFunction'''
    if text.count(old) != 1:
        raise SystemExit("host trampoline implementation marker missing")
    thunks_cpp.write_text(text.replace(old, new, 1))

    smc = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp"
    text = smc.read_text()
    if '#include "Thunks.h"' not in text:
        anchor = '#include "LinuxSyscalls/Syscalls.h"\n'
        if text.count(anchor) != 1:
            raise SystemExit("SyscallsSMCTracking include anchor missing")
        text = text.replace(anchor, anchor + '#include "Thunks.h"\n', 1)

    old = """  bool PendingResourceDeletion;

  {
    // Frontend calls this with nullptr Thread during initialization."""
    new = """  bool PendingResourceDeletion;

  // Callback trampolines contain guest executable addresses. Tombstone any
  // FEX-owned host trampoline that depends on the retiring range while those
  // guest addresses are still mapped.
  if (Thread && Size) {
    if (auto* Thunks = GetThunkHandler()) {
      Thunks->RetireGuestRange(Thread, reinterpret_cast<uintptr_t>(addr), Size);
    }
  }

  {
    // Frontend calls this with nullptr Thread during initialization."""
    if text.count(old) != 1:
        raise SystemExit("GuestMunmap pre-unmap marker missing")
    smc.write_text(text.replace(old, new, 1))

    print("Applied host->guest callback tombstone diagnostic")


if __name__ == "__main__":
    main()
