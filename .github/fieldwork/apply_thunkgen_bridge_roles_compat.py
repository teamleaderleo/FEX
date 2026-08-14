#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")

p = Path(sys.argv[1]).resolve() / "ThunkLibs/Generator/analysis.cpp"
s = p.read_text()
old = 'throw report_error(*decl->getSourceRange().getBegin().getPtrEncoding(), "Expected a prototype function-pointer type");'
new = 'throw report_error(decl->getLocation(), "Expected a prototype function-pointer type");'
if s.count(old) != 1:
    raise SystemExit(f"expected one Clang-18 diagnostic-location anchor, got {s.count(old)}")
p.write_text(s.replace(old, new, 1))
print("Applied bridge-role Clang diagnostic compatibility fix")
