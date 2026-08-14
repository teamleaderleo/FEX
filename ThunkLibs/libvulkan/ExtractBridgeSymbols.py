#!/usr/bin/env python3
from pathlib import Path
import re
import sys

src = Path(sys.argv[1]).read_text().splitlines()
inside = False
names = []
for line in src:
    if line.startswith("#define FOREACH_internal_SYMBOL(EXPAND)"):
        inside = True
        continue
    if inside:
        m = re.search(r"EXPAND\(([^,]+),", line)
        if m:
            names.append(m.group(1).strip())
            continue
        if names:
            break
if not names:
    raise SystemExit("FOREACH_internal_SYMBOL not found")
Path(sys.argv[2]).write_text("".join(f"VULKAN_BRIDGE_SYMBOL({name})\n" for name in names))
print(f"extracted {len(names)} Vulkan internal symbols")
