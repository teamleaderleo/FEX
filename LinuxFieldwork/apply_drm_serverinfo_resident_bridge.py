from pathlib import Path

# Start with the already-proven two-stage persistent callback conversion.
base = Path('LinuxFieldwork/apply_drm_serverinfo_callback_bridge.py').read_text()
exec(compile(base, 'apply_drm_serverinfo_callback_bridge.py', 'exec'))

# Add a tiny resident guest sidecar that owns only the fixed FEX callback
# unpacker for drmServerInfo::load_module: int(const char *).
bridge_dir = Path('ThunkLibs/libdrm_bridge')
bridge_dir.mkdir(exist_ok=True)
(bridge_dir / 'libdrm_bridge_interface.cpp').write_text(r'''#include <common/GeneratorInterface.h>

using LoadModuleSignature = int(const char*);

template<typename>
struct fex_gen_type {};

template<>
struct fex_gen_type<LoadModuleSignature> {};
''')

(bridge_dir / 'Guest.cpp').write_text(r'''#include "common/Guest.h"
#include "thunkgen_guest_libdrm_bridge.inl"

extern "C" uintptr_t FEXDRMBridgeLoadModuleUnpacker() {
  using LoadModuleSignature = int(const char*);
  return reinterpret_cast<uintptr_t>(CallbackUnpack<LoadModuleSignature>::Unpack);
}
''')

# Build the sidecar as NODELETE, link it into the ordinary DRM wrapper, but
# leave the wrapper itself unloadable.
cmake = Path('ThunkLibs/GuestLibs/CMakeLists.txt')
s = cmake.read_text()
old = '''  generate(libdrm ${CMAKE_CURRENT_SOURCE_DIR}/../libdrm/libdrm_interface.cpp)\n  target_include_directories_from_pkgconfig(libdrm-guest-deps libdrm /usr/include/drm /usr/include/libdrm)\n  add_guest_lib(drm "libdrm.so.2")\n'''
new = '''  generate(libdrm_bridge ${CMAKE_CURRENT_SOURCE_DIR}/../libdrm_bridge/libdrm_bridge_interface.cpp)\n  add_guest_lib(drm_bridge "libfex-drm-bridge.so")\n  set_target_properties(drm_bridge-guest PROPERTIES OUTPUT_NAME "fex-drm-bridge")\n  if (TARGET_TYPE STREQUAL "SHARED")\n    target_link_options(drm_bridge-guest PRIVATE "LINKER:-z,nodelete")\n  endif()\n\n  generate(libdrm ${CMAKE_CURRENT_SOURCE_DIR}/../libdrm/libdrm_interface.cpp)\n  target_include_directories_from_pkgconfig(libdrm-guest-deps libdrm /usr/include/drm /usr/include/libdrm)\n  add_guest_lib(drm "libdrm.so.2")\n  target_link_libraries(drm-guest PRIVATE drm_bridge-guest)\n'''
assert s.count(old) == 1, s.count(old)
cmake.write_text(s.replace(old, new, 1))

# Replace the wrapper-local CallbackUnpack address with the resident sidecar's
# unpacker address. The application callback target remains unchanged.
guest = Path('ThunkLibs/libdrm/Guest.cpp')
s = guest.read_text()
needle = '#include "thunkgen_guest_libdrm.inl"\n\n'
assert s.count(needle) == 1, s.count(needle)
s = s.replace(needle, needle + 'extern "C" uintptr_t FEXDRMBridgeLoadModuleUnpacker();\n\n', 1)
old = '  host_info.load_module = AllocateHostTrampolineForGuestFunction(info->load_module);'
new = '''  using GuestUnpacker = void THUNK_ABI (*)(uintptr_t, void*);\n  auto resident_unpacker = reinterpret_cast<GuestUnpacker>(FEXDRMBridgeLoadModuleUnpacker());\n  host_info.load_module = AllocateHostTrampolineForGuestFunction(resident_unpacker, info->load_module);'''
assert s.count(old) == 1, s.count(old)
guest.write_text(s.replace(old, new, 1))
