#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()
    vulkan_dir = root / "ThunkLibs/libvulkan"
    guest_cpp = vulkan_dir / "Guest.cpp"
    cmake = root / "ThunkLibs/GuestLibs/CMakeLists.txt"

    bridge_cpp = r'''#define VK_USE_64_BIT_PTR_DEFINES 0
#define VK_USE_PLATFORM_XLIB_XRANDR_EXT
#define VK_USE_PLATFORM_XLIB_KHR
#define VK_USE_PLATFORM_XCB_KHR
#define VK_USE_PLATFORM_WAYLAND_KHR
#include <vulkan/vulkan.h>

#include "common/Guest.h"

#include <cstdint>
#include <string_view>
#include <unordered_map>

// Generated directly by thunkgen -guest-bridge. Contains only signature
// adapters and symbol enumerators; no API packing/public wrapper bodies.
#include "thunkgen_bridge_libvulkan.inl"

#define DECLARE_RESIDENT(name, unused) \
  static const uintptr_t resident_##name = reinterpret_cast<uintptr_t>(GetCallerForHostFunction(name));
FOREACH_internal_SYMBOL(DECLARE_RESIDENT)
#undef DECLARE_RESIDENT

extern "C" __attribute__((visibility("default")))
uintptr_t fex_vulkan_bridge_find_host_invoker(const char* name) {
  static const std::unordered_map<std::string_view, uintptr_t> Invokers = [] {
    std::unordered_map<std::string_view, uintptr_t> Ret;
#define ADD_RESIDENT(name, unused) Ret[#name] = resident_##name;
    FOREACH_internal_SYMBOL(ADD_RESIDENT)
#undef ADD_RESIDENT
    return Ret;
  }();
  auto It = Invokers.find(name);
  return It == Invokers.end() ? 0 : It->second;
}

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
'''
    (vulkan_dir / "GuestBridgeDirect.cpp").write_text(bridge_cpp)

    text = guest_cpp.read_text()
    old_map = '''// Maps Vulkan API function names to the address of a guest function which is\n// linked to the corresponding host function pointer\nconst std::unordered_map<std::string_view, uintptr_t /* guest function address */> HostPtrInvokers = std::invoke([]() {\n#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(GetCallerForHostFunction(name));\n  std::unordered_map<std::string_view, uintptr_t> Ret;\n  FOREACH_internal_SYMBOL(PAIR);\n  return Ret;\n#undef PAIR\n});\n\n'''
    if text.count(old_map) != 1:
        raise SystemExit('Vulkan HostPtrInvokers anchor mismatch')
    text = text.replace(old_map, '''uintptr_t fex_vulkan_bridge_find_host_invoker(const char* name);\nuintptr_t fex_vulkan_bridge_xsync_unpacker();\nuintptr_t fex_vulkan_bridge_xgetvisualinfo_unpacker();\nuintptr_t fex_vulkan_bridge_xdisplaystring_unpacker();\n\n''', 1)

    old_callable = '''static PFN_vkVoidFunction MakeGuestCallable(const char* origin, PFN_vkVoidFunction func, const char* name) {\n  auto It = HostPtrInvokers.find(name);\n  if (It == HostPtrInvokers.end()) {\n    fprintf(stderr, "%s: Unknown Vulkan function at address %p: %s\\n", origin, func, name);\n    if (stub_unknown_functions) {\n      const auto StubHostPtrInvoker = CallHostFunction<FatalError, void>;\n      LinkAddressToFunction((uintptr_t)func, reinterpret_cast<uintptr_t>(StubHostPtrInvoker));\n      return func;\n    }\n    return nullptr;\n  }\n  fprintf(stderr, "Linking address %p to host invoker %#zx\\n", func, It->second);\n  LinkAddressToFunction((uintptr_t)func, It->second);\n  return func;\n}\n'''
    new_callable = '''static PFN_vkVoidFunction MakeGuestCallable(const char* origin, PFN_vkVoidFunction func, const char* name) {\n  const auto GuestInvoker = fex_vulkan_bridge_find_host_invoker(name);\n  if (!GuestInvoker) {\n    fprintf(stderr, "%s: Unknown Vulkan function at address %p: %s\\n", origin, func, name);\n    if (stub_unknown_functions) {\n      const auto StubHostPtrInvoker = CallHostFunction<FatalError, void>;\n      LinkAddressToFunction((uintptr_t)func, reinterpret_cast<uintptr_t>(StubHostPtrInvoker));\n      return func;\n    }\n    return nullptr;\n  }\n  fprintf(stderr, "Linking address %p to direct-generated resident host invoker %#zx\\n", func, GuestInvoker);\n  LinkAddressToFunction((uintptr_t)func, GuestInvoker);\n  return func;\n}\n'''
    if text.count(old_callable) != 1:
        raise SystemExit('Vulkan MakeGuestCallable anchor mismatch')
    text = text.replace(old_callable, new_callable, 1)

    old_init = '''  fexfn_pack_Vulkan_SetGuestXSync((uintptr_t)dlsym(libx11, "XSync"), (uintptr_t)CallbackUnpack<decltype(XSync)>::Unpack);\n  fexfn_pack_Vulkan_SetGuestXGetVisualInfo((uintptr_t)dlsym(libx11, "XGetVisualInfo"), (uintptr_t)CallbackUnpack<decltype(XGetVisualInfo)>::Unpack);\n  fexfn_pack_Vulkan_SetGuestXDisplayString((uintptr_t)dlsym(libx11, "XDisplayString"), (uintptr_t)CallbackUnpack<decltype(XDisplayString)>::Unpack);\n'''
    new_init = '''  fexfn_pack_Vulkan_SetGuestXSync((uintptr_t)dlsym(libx11, "XSync"), fex_vulkan_bridge_xsync_unpacker());\n  fexfn_pack_Vulkan_SetGuestXGetVisualInfo((uintptr_t)dlsym(libx11, "XGetVisualInfo"), fex_vulkan_bridge_xgetvisualinfo_unpacker());\n  fexfn_pack_Vulkan_SetGuestXDisplayString((uintptr_t)dlsym(libx11, "XDisplayString"), fex_vulkan_bridge_xdisplaystring_unpacker());\n'''
    if text.count(old_init) != 1:
        raise SystemExit('Vulkan OnInit anchor mismatch')
    guest_cpp.write_text(text.replace(old_init, new_init, 1))

    cm = cmake.read_text()
    anchor = '''  add_guest_lib(vulkan "libvulkan.so.1")\n'''
    block = r'''  add_guest_lib(vulkan "libvulkan.so.1")

  if (CMAKE_CURRENT_SOURCE_DIR STREQUAL CMAKE_SOURCE_DIR)
    set(VULKAN_DIRECT_BRIDGE "${CMAKE_CURRENT_BINARY_DIR}/gen/thunkgen_bridge_libvulkan.inl")
    add_library(vulkan-bridge-guest SHARED
      "${CMAKE_CURRENT_SOURCE_DIR}/../libvulkan/GuestBridgeDirect.cpp"
      "${VULKAN_DIRECT_BRIDGE}")
    set_source_files_properties("${VULKAN_DIRECT_BRIDGE}" PROPERTIES GENERATED TRUE HEADER_FILE_ONLY TRUE)
    add_dependencies(vulkan-bridge-guest libvulkan-guest-bridge-gen)
    target_include_directories(vulkan-bridge-guest PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/gen/")
    target_compile_definitions(vulkan-bridge-guest PRIVATE GUEST_THUNK_LIBRARY)
    target_link_libraries(vulkan-bridge-guest PRIVATE libvulkan-guest-deps)
    target_compile_options(vulkan-bridge-guest PRIVATE -fwrapv -msse2 -mfpmath=sse)
    target_link_options(vulkan-bridge-guest PRIVATE "LINKER:-z,nodelete")
    set_target_properties(vulkan-bridge-guest PROPERTIES OUTPUT_NAME "fex-vulkan-bridge")
    target_link_libraries(vulkan-guest PRIVATE vulkan-bridge-guest)
    target_link_options(vulkan-guest PRIVATE "LINKER:-rpath,$ORIGIN")
    install(TARGETS vulkan-bridge-guest DESTINATION ${DATA_DIRECTORY}/GuestThunks/)
  endif()
'''
    if cm.count(anchor) != 1:
        raise SystemExit(f'Vulkan CMake anchor count={cm.count(anchor)}')
    cmake.write_text(cm.replace(anchor, block, 1))
    print('Applied Vulkan split using direct thunkgen bridge output')


if __name__ == '__main__':
    main()
