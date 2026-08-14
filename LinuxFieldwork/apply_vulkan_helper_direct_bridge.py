#!/usr/bin/env python3
from pathlib import Path


def replace_one(path: Path, old: str, new: str, label: str) -> None:
    s = path.read_text()
    count = s.count(old)
    assert count == 1, (label, count)
    path.write_text(s.replace(old, new, 1))

vulkan = Path('ThunkLibs/libvulkan')
guest = vulkan / 'Guest.cpp'
bridge = Path('ThunkLibs/libvulkan_bridge')
bridge.mkdir(exist_ok=True)
(bridge / 'Guest.cpp').write_text(r'''#define VK_USE_64_BIT_PTR_DEFINES 0
#define VK_USE_PLATFORM_XLIB_XRANDR_EXT
#define VK_USE_PLATFORM_XLIB_KHR
#define VK_USE_PLATFORM_XCB_KHR
#define VK_USE_PLATFORM_WAYLAND_KHR
#include <vulkan/vulkan.h>
#include "common/Guest.h"
#include "thunkgen_bridge_libvulkan.inl"

extern "C" uintptr_t fex_vulkan_bridge_xsync_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XSync)>::Unpack);
}
extern "C" uintptr_t fex_vulkan_bridge_xgetvisualinfo_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XGetVisualInfo)>::Unpack);
}
extern "C" uintptr_t fex_vulkan_bridge_xdisplaystring_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XDisplayString)>::Unpack);
}
''')

replace_one(
    guest,
    '#include "thunkgen_guest_libvulkan.inl"\n',
    '#include "thunkgen_bridge_accessors_libvulkan.inl"\n#include "thunkgen_guest_libvulkan.inl"\n',
    'guest generated include')
replace_one(
    guest,
    '#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(GetCallerForHostFunction(name));\n',
    '#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(FEXGetResidentCallerForHostFunction(name));\n',
    'HostPtrInvokers')

s = guest.read_text()
init_old = '''  fexfn_pack_Vulkan_SetGuestXSync((uintptr_t)dlsym(libx11, "XSync"), (uintptr_t)CallbackUnpack<decltype(XSync)>::Unpack);\n  fexfn_pack_Vulkan_SetGuestXGetVisualInfo((uintptr_t)dlsym(libx11, "XGetVisualInfo"), (uintptr_t)CallbackUnpack<decltype(XGetVisualInfo)>::Unpack);\n  fexfn_pack_Vulkan_SetGuestXDisplayString((uintptr_t)dlsym(libx11, "XDisplayString"), (uintptr_t)CallbackUnpack<decltype(XDisplayString)>::Unpack);\n'''
init_new = '''  fexfn_pack_Vulkan_SetGuestXSync((uintptr_t)dlsym(libx11, "XSync"), fex_vulkan_bridge_xsync_unpacker());\n  fexfn_pack_Vulkan_SetGuestXGetVisualInfo((uintptr_t)dlsym(libx11, "XGetVisualInfo"), fex_vulkan_bridge_xgetvisualinfo_unpacker());\n  fexfn_pack_Vulkan_SetGuestXDisplayString((uintptr_t)dlsym(libx11, "XDisplayString"), fex_vulkan_bridge_xdisplaystring_unpacker());\n'''
assert s.count(init_old) == 1, s.count(init_old)
decls = '''extern "C" uintptr_t fex_vulkan_bridge_xsync_unpacker();\nextern "C" uintptr_t fex_vulkan_bridge_xgetvisualinfo_unpacker();\nextern "C" uintptr_t fex_vulkan_bridge_xdisplaystring_unpacker();\n\n'''
# Put the bridge declarations immediately before OnInit so no unrelated public API changes.
oninit = 'void OnInit() {'
assert s.count(oninit) == 1
s = s.replace(oninit, decls + oninit, 1).replace(init_old, init_new, 1)
guest.write_text(s)

cmake = Path('ThunkLibs/GuestLibs/CMakeLists.txt')
c = cmake.read_text()
anchor = '  add_guest_lib(vulkan "libvulkan.so.1")\n'
replacement = '''  add_guest_lib(vulkan "libvulkan.so.1")\n  add_guest_bridge(vulkan_bridge "libfex-vulkan-bridge.so"\n    OUTPUT_NAME "fex-vulkan-bridge"\n    WRAPPER_TARGET vulkan-guest\n    GENERATOR libvulkan\n    DEP_TARGETS libvulkan-guest-deps)\n'''
assert c.count(anchor) == 1, c.count(anchor)
cmake.write_text(c.replace(anchor, replacement, 1))
print('Applied helper-backed Vulkan direct resident bridge')
