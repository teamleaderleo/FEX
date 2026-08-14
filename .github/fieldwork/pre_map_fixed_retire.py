#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")

    root = Path(sys.argv[1]).resolve()
    path = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/SyscallsSMCTracking.cpp"
    text = path.read_text()

    old = '''  uint64_t Result;\n  size_t Size = FEXCore::AlignUp(length, FEXCore::Utils::FEX_PAGE_SIZE);\n  std::optional<LateApplyExtendedVolatileMetadata> LateMetadata = std::nullopt;\n'''
    new = r'''  uint64_t Result;
  size_t Size = FEXCore::AlignUp(length, FEXCore::Utils::FEX_PAGE_SIZE);
  std::optional<LateApplyExtendedVolatileMetadata> LateMetadata = std::nullopt;

  // Diagnostic lifetime transaction: MAP_FIXED destroys any previous mapping
  // generation covering [addr, addr + Size). Retire dependent thunk claims
  // while the old generation is still present, before the host mmap can replace
  // it. This intentionally uses the current range-based claim index to test the
  // ordering/hook; production ownership should use a non-reusable VMA owner ID.
  //
  // Experimental limitation: this probe does not restore retired claims if the
  // subsequent host mmap fails. The controlled test uses a valid aligned
  // MAP_FIXED replacement, so failure rollback is left to the owner-token design.
  if (Thread && Size && (flags & MAP_FIXED) && addr) {
    if (auto* Thunks = GetThunkHandler()) {
      fprintf(stderr, "DIAG_MAP_FIXED_PREPARE range=%#lx+%#lx\n",
              reinterpret_cast<uintptr_t>(addr), Size);
      Thunks->RetireGuestRange(Thread, reinterpret_cast<uintptr_t>(addr), Size);
    }
  }
'''

    count = text.count(old)
    if count != 1:
        raise SystemExit(f"GuestMmap anchor: expected one match, found {count}")

    path.write_text(text.replace(old, new, 1))
    print("Added pre-MAP_FIXED thunk-claim retirement diagnostic")


if __name__ == "__main__":
    main()
