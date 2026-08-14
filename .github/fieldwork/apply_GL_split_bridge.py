#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    root = Path(sys.argv[1]).resolve()
    gl_dir = root / "ThunkLibs/libGL"
    guest_cpp = gl_dir / "libGL_Guest.cpp"
    cmake = root / "ThunkLibs/GuestLibs/CMakeLists.txt"

    source = guest_cpp.read_text()
    include_lines = []
    for line in source.splitlines():
        if line.startswith("#include") and "thunkgen_guest_libGL.inl" not in line:
            include_lines.append(line)
    if not any("common/Guest.h" in line for line in include_lines):
        raise SystemExit("libGL Guest.cpp include set did not contain common/Guest.h")

    extractor = r'''#!/usr/bin/env python3
from pathlib import Path
import sys
src = Path(sys.argv[1]).read_text().splitlines()
out = [line for line in src if line.lstrip().startswith("MAKE_CALLBACK_THUNK(")]
Path(sys.argv[2]).write_text("// Extracted GL callback/signature thunks for resident bridge.\n" + "\n".join(out) + "\n")
if not out:
    raise SystemExit("no MAKE_CALLBACK_THUNK lines found")
print(f"extracted {len(out)} callback thunk signatures")
'''
    (gl_dir / "ExtractBridgeThunks.py").write_text(extractor)

    symbols = r'''#!/usr/bin/env python3
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
Path(sys.argv[2]).write_text("".join(f"GL_BRIDGE_SYMBOL({name})\n" for name in names))
print(f"extracted {len(names)} GL internal symbols")
'''
    (gl_dir / "ExtractBridgeSymbols.py").write_text(symbols)

    bridge = "\n".join(include_lines) + r'''

#include <cstdint>
#include <string_view>
#include <unordered_map>

#include "GL_bridge_thunks.inl"

#define GL_BRIDGE_SYMBOL(name) \
  static const uintptr_t resident_##name = reinterpret_cast<uintptr_t>(GetCallerForHostFunction(name));
#include "GL_bridge_symbols.inl"
#undef GL_BRIDGE_SYMBOL

extern "C" __attribute__((visibility("default")))
uintptr_t fex_GL_bridge_find_host_invoker(const char* name) {
  static const std::unordered_map<std::string_view, uintptr_t> Invokers = [] {
    std::unordered_map<std::string_view, uintptr_t> Ret;
#define GL_BRIDGE_SYMBOL(symbol) Ret[#symbol] = resident_##symbol;
#include "GL_bridge_symbols.inl"
#undef GL_BRIDGE_SYMBOL
    return Ret;
  }();
  auto It = Invokers.find(name);
  return It == Invokers.end() ? 0 : It->second;
}
'''
    (gl_dir / "GuestBridge.cpp").write_text(bridge)

    marker = "const std::unordered_map<std::string_view, uintptr_t /* guest function address */> HostPtrInvokers = std::invoke([]() {\n"
    if source.count(marker) != 1:
        raise SystemExit(f"HostPtrInvokers marker count {source.count(marker)}")
    source = source.replace(marker, 'extern "C" uintptr_t fex_GL_bridge_find_host_invoker(const char* name);\n\n' + marker, 1)
    old_pair = '#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(GetCallerForHostFunction(name));'
    new_pair = '#define PAIR(name, unused) Ret[#name] = fex_GL_bridge_find_host_invoker(#name);'
    if source.count(old_pair) != 1:
        raise SystemExit(f"HostPtrInvokers PAIR count {source.count(old_pair)}")
    guest_cpp.write_text(source.replace(old_pair, new_pair, 1))

    cm = cmake.read_text()
    anchor = '  add_guest_lib(GL "libGL.so.1")\n'
    if cm.count(anchor) != 1:
        raise SystemExit(f"GL CMake anchor count {cm.count(anchor)}")
    block = r'''  add_guest_lib(GL "libGL.so.1")

  if (CMAKE_CURRENT_SOURCE_DIR STREQUAL CMAKE_SOURCE_DIR)
    set(GL_BRIDGE_THUNKS "${CMAKE_CURRENT_BINARY_DIR}/gen/GL_bridge_thunks.inl")
    set(GL_BRIDGE_SYMBOLS "${CMAKE_CURRENT_BINARY_DIR}/gen/GL_bridge_symbols.inl")
    add_custom_command(
      OUTPUT "${GL_BRIDGE_THUNKS}" "${GL_BRIDGE_SYMBOLS}"
      DEPENDS ${GEN_libGL}
              "${CMAKE_CURRENT_SOURCE_DIR}/../libGL/ExtractBridgeThunks.py"
              "${CMAKE_CURRENT_SOURCE_DIR}/../libGL/ExtractBridgeSymbols.py"
      COMMAND python3 "${CMAKE_CURRENT_SOURCE_DIR}/../libGL/ExtractBridgeThunks.py"
              "${CMAKE_CURRENT_BINARY_DIR}/gen/thunkgen_guest_libGL.inl" "${GL_BRIDGE_THUNKS}"
      COMMAND python3 "${CMAKE_CURRENT_SOURCE_DIR}/../libGL/ExtractBridgeSymbols.py"
              "${CMAKE_CURRENT_BINARY_DIR}/gen/thunkgen_guest_libGL.inl" "${GL_BRIDGE_SYMBOLS}"
      VERBATIM)
    add_library(GL-bridge-guest SHARED
      "${CMAKE_CURRENT_SOURCE_DIR}/../libGL/GuestBridge.cpp"
      "${GL_BRIDGE_THUNKS}"
      "${GL_BRIDGE_SYMBOLS}")
    set_source_files_properties("${GL_BRIDGE_THUNKS}" "${GL_BRIDGE_SYMBOLS}" PROPERTIES GENERATED TRUE HEADER_FILE_ONLY TRUE)
    target_include_directories(GL-bridge-guest PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/gen/")
    target_compile_definitions(GL-bridge-guest PRIVATE GUEST_THUNK_LIBRARY)
    target_link_libraries(GL-bridge-guest PRIVATE libGL-guest-deps)
    target_compile_options(GL-bridge-guest PRIVATE -fwrapv -msse2 -mfpmath=sse)
    target_link_options(GL-bridge-guest PRIVATE "LINKER:-z,nodelete")
    set_target_properties(GL-bridge-guest PROPERTIES OUTPUT_NAME "fex-GL-bridge")
    target_link_libraries(GL-guest PRIVATE GL-bridge-guest)
    target_link_options(GL-guest PRIVATE "LINKER:-rpath,$ORIGIN")
    install(TARGETS GL-bridge-guest DESTINATION ${DATA_DIRECTORY}/GuestThunks/)
  endif()
'''
    cmake.write_text(cm.replace(anchor, block, 1))
    print("Applied GL split resident bridge prototype")


if __name__ == "__main__":
    main()
