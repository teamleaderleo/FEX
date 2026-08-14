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
    p = root / "Source/Tools/LinuxEmulation/Thunks.cpp"
    text = p.read_text()
    if "#include <cstdlib>" not in text:
        text = text.replace("#include <cstdint>\n", "#include <cstdint>\n#include <cstdlib>\n", 1)

    old = r'''void ThunkHandler_impl::RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) {
  if (!Thread || !Length) return;

  struct Transition {'''
    new = r'''static void IntegratedRevokedGuestCallback(void* GuestUnpacker, void* GuestTarget, void* ArgsRV) {
  (void)GuestUnpacker;
  (void)GuestTarget;
  (void)ArgsRV;
  fprintf(stderr, "DIAG_INTEGRATED_CALLBACK_REVOKED invoked\n");
  std::_Exit(113);
}

void ThunkHandler_impl::RetireGuestRange(FEXCore::Core::InternalThreadState* Thread, uintptr_t Base, uintptr_t Length) {
  if (!Thread || !Length) return;

  struct Transition {'''
    if old not in text:
        raise SystemExit("RetireGuestRange head anchor missing")
    text = text.replace(old, new, 1)

    old = r'''  {
    std::lock_guard lk(ThunksMutex);
    for (auto it = LinkedHostClaims.begin(); it != LinkedHostClaims.end();) {'''
    new = r'''  {
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
              "DIAG_INTEGRATED_CALLBACK_TOMBSTONE trampoline=%p unpacker=%#lx target=%#lx range=%#lx+%#lx\n",
              Trampoline, Unpacker, Target, Base, Length);
      Info.CallCallback = reinterpret_cast<uintptr_t>(&IntegratedRevokedGuestCallback);
      Info.GuestUnpacker = 0;
      Info.GuestTarget = 0;
      It = GuestcallToHostTrampoline.erase(It);
    }

    for (auto it = LinkedHostClaims.begin(); it != LinkedHostClaims.end();) {'''
    if old not in text:
        raise SystemExit("claim-lock anchor missing")
    text = text.replace(old, new, 1)
    p.write_text(text)
    print("Merged callback tombstoning into multi-owner pre-unmap retirement")


if __name__ == "__main__":
    main()
