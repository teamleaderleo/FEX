#!/usr/bin/env python3
from pathlib import Path
import sys


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()

    vulkan_dir = root / "ThunkLibs/libvulkan"
    guest_cpp = vulkan_dir / "Guest.cpp"
    cmake = root / "ThunkLibs/GuestLibs/CMakeLists.txt"

    extractor = r'''#!/usr/bin/env python3
from pathlib import Path
import sys

src = Path(sys.argv[1]).read_text().splitlines()
out = []
for line in src:
    stripped = line.lstrip()
    if stripped.startswith("MAKE_CALLBACK_THUNK("):
        out.append(line)
Path(sys.argv[2]).write_text("// Extracted signature-specific callback thunks for resident bridge.\\n" + "\\n".join(out) + "\\n")
if not out:
    raise SystemExit("no MAKE_CALLBACK_THUNK lines found")
print(f"extracted {len(out)} callback thunk signatures")
'''
    extractor_path = vulkan_dir / "ExtractBridgeThunks.py"
    extractor_path.write_text(extractor)

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

#include "vulkan_bridge_thunks.inl"

// The wrapper still owns library-specific state and host-pack calls. This DSO
// owns only addresses that may escape wrapper lifetime into FEX/host state.

#define DECLARE_RESIDENT_CALLER(name, unused) \
  static auto resident_##name = reinterpret_cast<uintptr_t>(GetCallerForHostFunction(name));

// The full generated symbol enumerator remains in the wrapper's inl. The
// transformer generates this declaration list from the same interface below.
#include "vulkan_bridge_symbols.inl"

extern "C" __attribute__((visibility("default")))
uintptr_t fex_vulkan_bridge_find_host_invoker(const char* name) {
  static const std::unordered_map<std::string_view, uintptr_t> Invokers = [] {
    std::unordered_map<std::string_view, uintptr_t> Ret;
#define ADD_RESIDENT_CALLER(name, unused) Ret[#name] = resident_##name;
    FOREACH_VULKAN_BRIDGE_SYMBOL(ADD_RESIDENT_CALLER)
#undef ADD_RESIDENT_CALLER
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
    (vulkan_dir / "GuestBridge.cpp").write_text(bridge_cpp)

    symbol_extractor = r'''#!/usr/bin/env python3
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
        m = re.search(r"EXPAND\\(([^,]+),", line)
        if m:
            names.append(m.group(1).strip())
            continue
        if names:
            break
if not names:
    raise SystemExit("FOREACH_internal_SYMBOL not found")
lines = ["#pragma once", "#define FOREACH_VULKAN_BRIDGE_SYMBOL(EXPAND) \\\"]
for i, name in enumerate(names):
    tail = " \\\" if i + 1 != len(names) else ""
    lines.append(f"  EXPAND({name}, unused){tail}")
lines += ["", "FOREACH_VULKAN_BRIDGE_SYMBOL(DECLARE_RESIDENT_CALLER)", "#undef DECLARE_RESIDENT_CALLER", ""]
Path(sys.argv[2]).write_text("\\n".join(lines))
print(f"extracted {len(names)} Vulkan internal symbols")
'''
    (vulkan_dir / "ExtractBridgeSymbols.py").write_text(symbol_extractor)

    old_map = '''// Maps Vulkan API function names to the address of a guest function which is\n// linked to the corresponding host function pointer\nconst std::unordered_map<std::string_view, uintptr_t /* guest function address */> HostPtrInvokers = std::invoke([]() {\n#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(GetCallerForHostFunction(name));\n  std::unordered_map<std::string_view, uintptr_t> Ret;\n  FOREACH_internal_SYMBOL(PAIR);\n  return Ret;\n#undef PAIR\n});\n\n'''
    text = guest_cpp.read_text()
    if text.count(old_map) != 1:
        raise SystemExit("HostPtrInvokers block anchor missing")
    text = text.replace(old_map, '''extern uintptr_t fex_vulkan_bridge_find_host_invoker(const char* name);\nextern uintptr_t fex_vulkan_bridge_xsync_unpacker();\nextern uintptr_t fex_vulkan_bridge_xgetvisualinfo_unpacker();\nextern uintptr_t fex_vulkan_bridge_xdisplaystring_unpacker();\n\n''', 1)

    old_callable = '''static PFN_vkVoidFunction MakeGuestCallable(const char* origin, PFN_vkVoidFunction func, const char* name) {\n  auto It = HostPtrInvokers.find(name);\n  if (It == HostPtrInvokers.end()) {\n    fprintf(stderr, "%s: Unknown Vulkan function at address %p: %s\\n", origin, func, name);\n    if (stub_unknown_functions) {\n      const auto StubHostPtrInvoker = CallHostFunction<FatalError, void>;\n      LinkAddressToFunction((uintptr_t)func, reinterpret_cast<uintptr_t>(StubHostPtrInvoker));\n      return func;\n    }\n    return nullptr;\n  }\n  fprintf(stderr, "Linking address %p to host invoker %#zx\\n", func, It->second);\n  LinkAddressToFunction((uintptr_t)func, It->second);\n  return func;\n}\n'''
    new_callable = '''static PFN_vkVoidFunction MakeGuestCallable(const char* origin, PFN_vkVoidFunction func, const char* name) {\n  const auto GuestInvoker = fex_vulkan_bridge_find_host_invoker(name);\n  if (!GuestInvoker) {\n    fprintf(stderr, "%s: Unknown Vulkan function at address %p: %s\\n", origin, func, name);\n    if (stub_unknown_functions) {\n      const auto StubHostPtrInvoker = CallHostFunction<FatalError, void>;\n      LinkAddressToFunction((uintptr_t)func, reinterpret_cast<uintptr_t>(StubHostPtrInvoker));\n      return func;\n    }\n    return nullptr;\n  }\n  fprintf(stderr, "Linking address %p to resident host invoker %#zx\\n", func, GuestInvoker);\n  LinkAddressToFunction((uintptr_t)func, GuestInvoker);\n  return func;\n}\n'''
    if text.count(old_callable) != 1:
        raise SystemExit("MakeGuestCallable anchor missing")
    text = text.replace(old_callable, new_callable, 1)

    old_init = '''  fexfn_pack_Vulkan_SetGuestXSync((uintptr_t)dlsym(libx11, "XSync"), (uintptr_t)CallbackUnpack<decltype(XSync)>::Unpack);\n  fexfn_pack_Vulkan_SetGuestXGetVisualInfo((uintptr_t)dlsym(libx11, "XGetVisualInfo"), (uintptr_t)CallbackUnpack<decltype(XGetVisualInfo)>::Unpack);\n  fexfn_pack_Vulkan_SetGuestXDisplayString((uintptr_t)dlsym(libx11, "XDisplayString"), (uintptr_t)CallbackUnpack<decltype(XDisplayString)>::Unpack);\n'''
    new_init = '''  fexfn_pack_Vulkan_SetGuestXSync((uintptr_t)dlsym(libx11, "XSync"), fex_vulkan_bridge_xsync_unpacker());\n  fexfn_pack_Vulkan_SetGuestXGetVisualInfo((uintptr_t)dlsym(libx11, "XGetVisualInfo"), fex_vulkan_bridge_xgetvisualinfo_unpacker());\n  fexfn_pack_Vulkan_SetGuestXDisplayString((uintptr_t)dlsym(libx11, "XDisplayString"), fex_vulkan_bridge_xdisplaystring_unpacker());\n'''
    if text.count(old_init) != 1:
        raise SystemExit("OnInit unpacker anchor missing")
    guest_cpp.write_text(text.replace(old_init, new_init, 1))

    cm = cmake.read_text()
    anchor = '''  add_guest_lib(vulkan "libvulkan.so.1")\n'''
    block = r'''  add_guest_lib(vulkan "libvulkan.so.1")

  if (CMAKE_CURRENT_SOURCE_DIR STREQUAL CMAKE_SOURCE_DIR)
    set(VULKAN_BRIDGE_THUNKS "${CMAKE_CURRENT_BINARY_DIR}/gen/vulkan_bridge_thunks.inl")
    set(VULKAN_BRIDGE_SYMBOLS "${CMAKE_CURRENT_BINARY_DIR}/gen/vulkan_bridge_symbols.inl")
    add_custom_command(
      OUTPUT "${VULKAN_BRIDGE_THUNKS}" "${VULKAN_BRIDGE_SYMBOLS}"
      DEPENDS ${GEN_libvulkan}
              "${CMAKE_CURRENT_SOURCE_DIR}/../libvulkan/ExtractBridgeThunks.py"
              "${CMAKE_CURRENT_SOURCE_DIR}/../libvulkan/ExtractBridgeSymbols.py"
      COMMAND python3 "${CMAKE_CURRENT_SOURCE_DIR}/../libvulkan/ExtractBridgeThunks.py"
              "${CMAKE_CURRENT_BINARY_DIR}/gen/thunkgen_guest_libvulkan.inl" "${VULKAN_BRIDGE_THUNKS}"
      COMMAND python3 "${CMAKE_CURRENT_SOURCE_DIR}/../libvulkan/ExtractBridgeSymbols.py"
              "${CMAKE_CURRENT_BINARY_DIR}/gen/thunkgen_guest_libvulkan.inl" "${VULKAN_BRIDGE_SYMBOLS}"
      VERBATIM)
    add_library(vulkan-bridge-guest SHARED
      "${CMAKE_CURRENT_SOURCE_DIR}/../libvulkan/GuestBridge.cpp"
      "${VULKAN_BRIDGE_THUNKS}"
      "${VULKAN_BRIDGE_SYMBOLS}")
    set_source_files_properties("${VULKAN_BRIDGE_THUNKS}" "${VULKAN_BRIDGE_SYMBOLS}" PROPERTIES GENERATED TRUE HEADER_FILE_ONLY TRUE)
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
        raise SystemExit("Vulkan CMake anchor missing")
    cmake.write_text(cm.replace(anchor, block, 1))

    print("Applied Vulkan split resident bridge prototype")


if __name__ == "__main__":
    main()
