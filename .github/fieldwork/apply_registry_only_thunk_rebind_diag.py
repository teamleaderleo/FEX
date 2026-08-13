#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    core = Path(sys.argv[1]).resolve() / "FEXCore/Source/Interface/Core/Core.cpp"
    text = core.read_text()
    old = '''    if (Result->Data != (void*)GuestThunkEntrypoint) {
      // NOTE: This may happen in Vulkan thunks if the Vulkan driver resolves two different symbols
      //       to the same function (e.g. vkGetPhysicalDeviceFeatures2/vkGetPhysicalDeviceFeatures2KHR)
      LogMan::Msg::EFmt("Input address for AddThunkTrampoline is already linked elsewhere");
    }'''
    if text.count(old) != 1:
        raise SystemExit(f"duplicate block: expected one anchor, found {text.count(old)}")
    new = '''    if (Result->Data != (void*)GuestThunkEntrypoint) {
      fprintf(stderr, "DIAG_REGISTRY_ONLY_DUP H=%#lx OLD=%p NEW=%#lx\\n", Entrypoint, Result->Data, GuestThunkEntrypoint);
      RemoveCustomIREntrypoint(nullptr, Entrypoint);
      AddThunkTrampolineIRHandler(Entrypoint, GuestThunkEntrypoint);
      return;
    }'''
    core.write_text(text.replace(old, new, 1))
    print("Applied registry-only duplicate-H rebind diagnostic")


if __name__ == "__main__":
    main()
