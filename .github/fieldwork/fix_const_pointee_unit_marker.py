#!/usr/bin/env python3
from pathlib import Path

p = Path('unittests/ThunkLibs/generator.cpp')
text = p.read_text()
old = '                                     "template<> struct fex_gen_config<func> {};\\n";\n'
new = '                                     "template<> struct fex_gen_config<func> : fexgen::custom_host_impl {};\\n";\n'
count = text.count(old)
if count != 1:
    raise SystemExit(f'expected one const-test marker anchor, found {count}')
p.write_text(text.replace(old, new, 1))
print('patched const-pointee unit to use the StructRepacking host-impl marker')
