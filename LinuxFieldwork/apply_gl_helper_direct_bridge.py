#!/usr/bin/env python3
from pathlib import Path
import re


def replace_one(path: Path, old: str, new: str, label: str) -> None:
    s = path.read_text()
    count = s.count(old)
    assert count == 1, (label, count)
    path.write_text(s.replace(old, new, 1))


guest = Path('ThunkLibs/libGL/libGL_Guest.cpp')

replace_one(
    guest,
    '#include "thunkgen_guest_libGL.inl"\n',
    '#include "thunkgen_bridge_accessors_libGL.inl"\n#include "thunkgen_guest_libGL.inl"\n',
    'generated include')

s = guest.read_text()
old_pair = '#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(GetCallerForHostFunction(name));'
new_pair = '#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(FEXGetResidentCallerForHostFunction(name));'
assert s.count(old_pair) == 1, s.count(old_pair)
s = s.replace(old_pair, new_pair, 1)

# The old allocator target is wrapper-local executable code. Leave the public
# GL wrapper state where it is, but publish the address of the resident target.
s, malloc_count = re.subn(
    r'static\s+void\s*\*\s*malloc_wrapper\s*\([^)]*\)\s*\{[^{}]*\}\s*',
    '', s, count=1, flags=re.S)
assert malloc_count == 1, malloc_count

# Replace fixed X11 unpacker and allocator targets in OnInit. These exact forms
# are stable in the FEX product source used by this diagnostic.
replacements = {
    '(uintptr_t)CallbackUnpack<decltype(XSync)>::Unpack': 'fex_gl_bridge_xsync_unpacker()',
    '(uintptr_t)CallbackUnpack<decltype(XGetVisualInfo)>::Unpack': 'fex_gl_bridge_xgetvisualinfo_unpacker()',
    '(uintptr_t)CallbackUnpack<decltype(XDisplayString)>::Unpack': 'fex_gl_bridge_xdisplaystring_unpacker()',
    '(uintptr_t)malloc_wrapper': '(uintptr_t)&FEXGLBridgeMalloc',
}
for old, new in replacements.items():
    assert s.count(old) == 1, (old, s.count(old))
    s = s.replace(old, new, 1)

decls = '''extern "C" void* FEXGLBridgeMalloc(size_t size);\nextern "C" uintptr_t fex_gl_bridge_xsync_unpacker();\nextern "C" uintptr_t fex_gl_bridge_xgetvisualinfo_unpacker();\nextern "C" uintptr_t fex_gl_bridge_xdisplaystring_unpacker();\n\n'''
oninit = 'void OnInit() {'
assert s.count(oninit) == 1, s.count(oninit)
s = s.replace(oninit, decls + oninit, 1)
guest.write_text(s)

bridge = Path('ThunkLibs/libGL_bridge')
bridge.mkdir(exist_ok=True)
(bridge / 'Guest.cpp').write_text(r'''// SPDX-License-Identifier: MIT
#include <cstdlib>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include "common/Guest.h"
#include "thunkgen_bridge_libGL.inl"

extern "C" void* FEXGLBridgeMalloc(size_t size) {
  return std::malloc(size);
}
extern "C" uintptr_t fex_gl_bridge_xsync_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XSync)>::Unpack);
}
extern "C" uintptr_t fex_gl_bridge_xgetvisualinfo_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XGetVisualInfo)>::Unpack);
}
extern "C" uintptr_t fex_gl_bridge_xdisplaystring_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XDisplayString)>::Unpack);
}
''')

cmake = Path('ThunkLibs/GuestLibs/CMakeLists.txt')
c = cmake.read_text()
anchor = 'add_guest_lib(GL "libGL.so.1")\n'
replacement = '''add_guest_lib(GL "libGL.so.1")\nadd_guest_bridge(GL_bridge "libfex-GL-bridge.so"\n  OUTPUT_NAME "fex-GL-bridge"\n  WRAPPER_TARGET GL-guest\n  GENERATOR libGL\n  DEP_TARGETS libGL-guest-deps)\n'''
assert c.count(anchor) == 1, c.count(anchor)
cmake.write_text(c.replace(anchor, replacement, 1))

print('Applied helper-backed direct GL resident bridge')
