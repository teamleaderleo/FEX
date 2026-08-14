#!/usr/bin/env python3
from pathlib import Path
import sys

src = Path(sys.argv[1]).read_text().splitlines()
out = [line for line in src if line.lstrip().startswith("MAKE_CALLBACK_THUNK(")]
Path(sys.argv[2]).write_text("// Extracted signature-specific callback thunks for resident bridge.\n" + "\n".join(out) + "\n")
if not out:
    raise SystemExit("no MAKE_CALLBACK_THUNK lines found")
print(f"extracted {len(out)} callback thunk signatures")
