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

    # Keep only the signature-specific generated CallHostFunction adapter
    # definitions required by GetCallerForHostFunction. Do not duplicate the
    # full generated guest wrapper into the resident companion.
    (gl_dir / "ExtractBridgeThunks.py").write_text(r'''#!/usr/bin/env python3
from pathlib import Path
import sys
src = Path(sys.argv[1]).read_text().splitlines()
out = [line for line in src if line.lstrip().startswith("MAKE_CALLBACK_THUNK(")]
if not out:
    raise SystemExit("no MAKE_CALLBACK_THUNK lines found")
Path(sys.argv[2]).write_text("// Extracted GL signature adapters.\n" + "\n".join(out) + "\n")
print(f"extracted {len(out)} GL callback thunk signatures")
''')

    (gl_dir / "ExtractBridgeSymbols.py").write_text(r'''#!/usr/bin/env python3
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
''')

    (gl_dir / "ResidentBridge.cpp").write_text(r'''#define GL_GLEXT_PROTOTYPES 1
#define GLX_GLXEXT_PROTOTYPES 1
#include <GL/glx.h>
#include <GL/glxext.h>
#include <GL/gl.h>
#include <GL/glext.h>
#undef GL_ARB_viewport_array
#include "glcorearb.h"
#include <X11/Xlib.h>
#include <X11/Xutil.h>

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string_view>
#include <unordered_map>

#include "common/Guest.h"
#include "gl_bridge_thunks.inl"

#define GL_BRIDGE_SYMBOL(name) \
  static const uintptr_t resident_##name = reinterpret_cast<uintptr_t>(GetCallerForHostFunction(name));
#include "gl_bridge_symbols.inl"
#undef GL_BRIDGE_SYMBOL

extern "C" __attribute__((visibility("default")))
uintptr_t FEXGLBridgeLookup(const char* name) {
  static const std::unordered_map<std::string_view, uintptr_t> Invokers = [] {
    std::unordered_map<std::string_view, uintptr_t> Ret;
#define GL_BRIDGE_SYMBOL(symbol) Ret[#symbol] = resident_##symbol;
#include "gl_bridge_symbols.inl"
#undef GL_BRIDGE_SYMBOL
    return Ret;
  }();
  auto It = Invokers.find(name);
  return It == Invokers.end() ? 0 : It->second;
}

// GL's host thunk stores a GuestMalloc callback for process-long use. The
// target as well as the unpacker therefore belong in the resident companion.
static void* ResidentMalloc(size_t size) {
  fprintf(stderr, "GL_BRIDGE_MALLOC size=%zu\n", size);
  return malloc(size);
}

extern "C" __attribute__((visibility("default")))
uintptr_t FEXGLBridgeMallocTarget() {
  return reinterpret_cast<uintptr_t>(&ResidentMalloc);
}
extern "C" __attribute__((visibility("default")))
uintptr_t FEXGLBridgeMallocUnpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(ResidentMalloc)>::Unpack);
}
extern "C" __attribute__((visibility("default")))
uintptr_t FEXGLBridgeXSyncUnpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XSync)>::Unpack);
}
extern "C" __attribute__((visibility("default")))
uintptr_t FEXGLBridgeXGetVisualInfoUnpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XGetVisualInfo)>::Unpack);
}
extern "C" __attribute__((visibility("default")))
uintptr_t FEXGLBridgeXDisplayStringUnpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XDisplayString)>::Unpack);
}
''')

    text = guest_cpp.read_text()
    anchor = '#include "thunkgen_guest_libGL.inl"\n\n'
    repl = '''#include "thunkgen_guest_libGL.inl"\n\nextern "C" uintptr_t FEXGLBridgeLookup(const char* name);\nextern "C" uintptr_t FEXGLBridgeMallocTarget();\nextern "C" uintptr_t FEXGLBridgeMallocUnpacker();\nextern "C" uintptr_t FEXGLBridgeXSyncUnpacker();\nextern "C" uintptr_t FEXGLBridgeXGetVisualInfoUnpacker();\nextern "C" uintptr_t FEXGLBridgeXDisplayStringUnpacker();\n\n'''
    if text.count(anchor) != 1:
        raise SystemExit("GL generated include anchor mismatch")
    text = text.replace(anchor, repl, 1)

    anchor = '  auto TargetFuncIt = HostPtrInvokers.find(reinterpret_cast<const char*>(procname));\n'
    repl = '''  const auto ResidentTarget = FEXGLBridgeLookup(reinterpret_cast<const char*>(procname));\n  if (ResidentTarget) {\n    fprintf(stderr, "GL_SPLIT_LINK name=%s H=%p T=%#zx\\n", procname, Ret, ResidentTarget);\n    LinkAddressToFunction((uintptr_t)Ret, ResidentTarget);\n    return Ret;\n  }\n\n  auto TargetFuncIt = HostPtrInvokers.find(reinterpret_cast<const char*>(procname));\n'''
    if text.count(anchor) != 1:
        raise SystemExit("GL proc lookup anchor mismatch")
    text = text.replace(anchor, repl, 1)

    old = '''static void OnInit() {\n  fexfn_pack_GL_SetGuestMalloc((uintptr_t)malloc_wrapper, (uintptr_t)CallbackUnpack<decltype(malloc_wrapper)>::Unpack);\n  fexfn_pack_GL_SetGuestXSync((uintptr_t)XSync, (uintptr_t)CallbackUnpack<decltype(XSync)>::Unpack);\n  fexfn_pack_GL_SetGuestXGetVisualInfo((uintptr_t)XGetVisualInfo, (uintptr_t)CallbackUnpack<decltype(XGetVisualInfo)>::Unpack);\n  fexfn_pack_GL_SetGuestXDisplayString((uintptr_t)XDisplayString, (uintptr_t)CallbackUnpack<decltype(XDisplayString)>::Unpack);\n}\n'''
    new = '''static void OnInit() {\n  fexfn_pack_GL_SetGuestMalloc(FEXGLBridgeMallocTarget(), FEXGLBridgeMallocUnpacker());\n  fexfn_pack_GL_SetGuestXSync((uintptr_t)XSync, FEXGLBridgeXSyncUnpacker());\n  fexfn_pack_GL_SetGuestXGetVisualInfo((uintptr_t)XGetVisualInfo, FEXGLBridgeXGetVisualInfoUnpacker());\n  fexfn_pack_GL_SetGuestXDisplayString((uintptr_t)XDisplayString, FEXGLBridgeXDisplayStringUnpacker());\n}\n'''
    if text.count(old) != 1:
        raise SystemExit("GL OnInit anchor mismatch")
    guest_cpp.write_text(text.replace(old, new, 1))

    cm = cmake.read_text()
    anchor = '''generate(libGL ${CMAKE_CURRENT_SOURCE_DIR}/../libGL/libGL_interface.cpp)\ntarget_include_directories_from_pkgconfig(libGL-guest-deps gl)\ntarget_include_directories_from_pkgconfig(libGL-guest-deps "xcb;x11;xrandr;xrender")\nadd_guest_lib(GL "libGL.so.1")\n'''
    block = r'''generate(libGL ${CMAKE_CURRENT_SOURCE_DIR}/../libGL/libGL_interface.cpp)
target_include_directories_from_pkgconfig(libGL-guest-deps gl)
target_include_directories_from_pkgconfig(libGL-guest-deps "xcb;x11;xrandr;xrender")

if (CMAKE_CURRENT_SOURCE_DIR STREQUAL CMAKE_SOURCE_DIR)
  set(GL_BRIDGE_THUNKS "${CMAKE_CURRENT_BINARY_DIR}/gen/gl_bridge_thunks.inl")
  set(GL_BRIDGE_SYMBOLS "${CMAKE_CURRENT_BINARY_DIR}/gen/gl_bridge_symbols.inl")
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
  add_library(GL-lifetime-bridge SHARED
    "${CMAKE_CURRENT_SOURCE_DIR}/../libGL/ResidentBridge.cpp"
    "${GL_BRIDGE_THUNKS}"
    "${GL_BRIDGE_SYMBOLS}")
  set_source_files_properties("${GL_BRIDGE_THUNKS}" "${GL_BRIDGE_SYMBOLS}" PROPERTIES GENERATED TRUE HEADER_FILE_ONLY TRUE)
  target_include_directories(GL-lifetime-bridge PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/gen/")
  target_compile_definitions(GL-lifetime-bridge PRIVATE GUEST_THUNK_LIBRARY)
  target_link_libraries(GL-lifetime-bridge PRIVATE libGL-guest-deps)
  target_compile_options(GL-lifetime-bridge PRIVATE -fwrapv -msse2 -mfpmath=sse)
  target_link_options(GL-lifetime-bridge PRIVATE "LINKER:-z,nodelete" "LINKER:-soname,libfex-GL-bridge.so")
  set_target_properties(GL-lifetime-bridge PROPERTIES OUTPUT_NAME "fex-GL-bridge" NO_SONAME ON)
endif()

add_guest_lib(GL "libGL.so.1")
if (CMAKE_CURRENT_SOURCE_DIR STREQUAL CMAKE_SOURCE_DIR)
  target_link_libraries(GL-guest PRIVATE GL-lifetime-bridge)
  target_link_options(GL-guest PRIVATE "LINKER:-rpath,$ORIGIN")
  install(TARGETS GL-lifetime-bridge DESTINATION ${DATA_DIRECTORY}/GuestThunks/)
endif()
'''
    if cm.count(anchor) != 1:
        raise SystemExit(f"GL CMake anchor count={cm.count(anchor)}")
    cmake.write_text(cm.replace(anchor, block, 1))
    print("Applied minimal GL split resident bridge prototype")


if __name__ == "__main__":
    main()
