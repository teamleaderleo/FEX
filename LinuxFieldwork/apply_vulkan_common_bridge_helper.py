#!/usr/bin/env python3
from pathlib import Path


def replace_one(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, got {count}")
    path.write_text(text.replace(old, new, 1))


vulkan = Path('ThunkLibs/libvulkan/Guest.cpp')
bridge_dir = Path('ThunkLibs/libvulkan_bridge')
bridge_dir.mkdir(exist_ok=True)

bridge_dir.joinpath('Guest.cpp').write_text(r'''#define VK_USE_64_BIT_PTR_DEFINES 0
#define VK_USE_PLATFORM_XLIB_XRANDR_EXT
#define VK_USE_PLATFORM_XLIB_KHR
#define VK_USE_PLATFORM_XCB_KHR
#define VK_USE_PLATFORM_WAYLAND_KHR
#include <vulkan/vulkan.h>

#include "common/Guest.h"
#include <cstdint>

// Role-aware thunkgen output owns the process-resident caller definitions.
#include "thunkgen_bridge_libvulkan.inl"

// Vulkan's fixed X11 callback families are semantic/custom ownership: they are
// retained by the host thunk even though they are not ordinary generated API
// function-pointer parameters.
extern "C" __attribute__((visibility("default")))
uintptr_t fex_vulkan_bridge_xsync_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XSync)>::Unpack);
}
extern "C" __attribute__((visibility("default")))
uintptr_t fex_vulkan_bridge_xgetvisualinfo_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XGetVisualInfo)>::Unpack);
}
extern "C" __attribute__((visibility("default")))
uintptr_t fex_vulkan_bridge_xdisplaystring_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XDisplayString)>::Unpack);
}
''')

replace_one(
    vulkan,
    '#include "thunkgen_guest_libvulkan.inl"\n',
    '#include "thunkgen_guest_libvulkan.inl"\n#include "thunkgen_bridge_accessors_libvulkan.inl"\n',
    'Vulkan generated include')

replace_one(
    vulkan,
    '#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(GetCallerForHostFunction(name));\n',
    '#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(FEXGetResidentCallerForHostFunction(name));\n',
    'Vulkan resident caller registry')

text = vulkan.read_text()
anchor = 'extern "C" {\n\n// Maps Vulkan API function names'
replacement = '''extern "C" {\n\nuintptr_t fex_vulkan_bridge_xsync_unpacker();\nuintptr_t fex_vulkan_bridge_xgetvisualinfo_unpacker();\nuintptr_t fex_vulkan_bridge_xdisplaystring_unpacker();\n\n// Maps Vulkan API function names'''
if text.count(anchor) != 1:
    raise SystemExit(f'Vulkan bridge declaration anchor count={text.count(anchor)}')
text = text.replace(anchor, replacement, 1)
old_init = '''  fexfn_pack_Vulkan_SetGuestXSync((uintptr_t)dlsym(libx11, "XSync"), (uintptr_t)CallbackUnpack<decltype(XSync)>::Unpack);\n  fexfn_pack_Vulkan_SetGuestXGetVisualInfo((uintptr_t)dlsym(libx11, "XGetVisualInfo"), (uintptr_t)CallbackUnpack<decltype(XGetVisualInfo)>::Unpack);\n  fexfn_pack_Vulkan_SetGuestXDisplayString((uintptr_t)dlsym(libx11, "XDisplayString"), (uintptr_t)CallbackUnpack<decltype(XDisplayString)>::Unpack);\n'''
new_init = '''  fexfn_pack_Vulkan_SetGuestXSync((uintptr_t)dlsym(libx11, "XSync"), fex_vulkan_bridge_xsync_unpacker());\n  fexfn_pack_Vulkan_SetGuestXGetVisualInfo((uintptr_t)dlsym(libx11, "XGetVisualInfo"), fex_vulkan_bridge_xgetvisualinfo_unpacker());\n  fexfn_pack_Vulkan_SetGuestXDisplayString((uintptr_t)dlsym(libx11, "XDisplayString"), fex_vulkan_bridge_xdisplaystring_unpacker());\n'''
if text.count(old_init) != 1:
    raise SystemExit(f'Vulkan X11 ownership anchor count={text.count(old_init)}')
vulkan.write_text(text.replace(old_init, new_init, 1))

cmake = Path('ThunkLibs/GuestLibs/CMakeLists.txt')
old = '''  generate(libvulkan ${CMAKE_CURRENT_SOURCE_DIR}/../libvulkan/libvulkan_interface.cpp)\n  target_include_directories(libvulkan-guest-deps INTERFACE ${FEX_PROJECT_SOURCE_DIR}/External/Vulkan-Headers/include/)\n  target_include_directories_from_pkgconfig(libvulkan-guest-deps "xcb;x11;xrandr;xrender")\n  add_guest_lib(vulkan "libvulkan.so.1")\n'''
new = '''  generate(libvulkan ${CMAKE_CURRENT_SOURCE_DIR}/../libvulkan/libvulkan_interface.cpp)\n  target_include_directories(libvulkan-guest-deps INTERFACE ${FEX_PROJECT_SOURCE_DIR}/External/Vulkan-Headers/include/)\n  target_include_directories_from_pkgconfig(libvulkan-guest-deps "xcb;x11;xrandr;xrender")\n  add_guest_lib(vulkan "libvulkan.so.1")\n  add_guest_bridge(vulkan_bridge "libfex-vulkan-bridge.so"\n    OUTPUT_NAME "fex-vulkan-bridge"\n    WRAPPER_TARGET vulkan-guest\n    GENERATOR libvulkan\n    DEP_TARGETS libvulkan-guest-deps\n    INCLUDE_DIRS "${CMAKE_CURRENT_SOURCE_DIR}/../libvulkan")\n'''
replace_one(cmake, old, new, 'Vulkan common bridge CMake wiring')

print('Applied role-aware Vulkan resident companion via common add_guest_bridge helper')
