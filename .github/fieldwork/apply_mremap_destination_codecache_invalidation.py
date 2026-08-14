#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")

    root = Path(sys.argv[1]).resolve()
    path = root / "Source/Tools/LinuxEmulation/LinuxSyscalls/Syscalls.h"
    text = path.read_text()

    old = '''      if (OldAddress != NewAddress) {\n        if (OldSize != 0) {\n          // This also handles the MREMAP_DONTUNMAP case\n          TM.InvalidateGuestCodeRange(Thread, OldAddress, OldSize);\n        }\n      } else {\n'''
    new = '''      if (OldAddress != NewAddress) {\n        if (OldSize != 0) {\n          // This also handles the MREMAP_DONTUNMAP case\n          TM.InvalidateGuestCodeRange(Thread, OldAddress, OldSize);\n        }\n        if (NewSize != 0) {\n          // A fixed remap may replace executable code at the destination.\n          // Drop any translation cached for that numeric guest address before\n          // the new mapping executes there. For a kernel-chosen free target,\n          // this invalidation is harmless.\n          TM.InvalidateGuestCodeRange(Thread, NewAddress, NewSize);\n        }\n      } else {\n'''

    if text.count(old) != 1:
        raise SystemExit(f"expected one remap invalidation anchor, found {text.count(old)}")
    path.write_text(text.replace(old, new, 1))
    print("Added code-cache invalidation for mremap destination range")


if __name__ == "__main__":
    main()
