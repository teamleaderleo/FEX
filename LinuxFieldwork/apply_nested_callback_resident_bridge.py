from pathlib import Path
import subprocess

# First apply the already-proven generic nested callback-member generator patch.
base = Path('LinuxFieldwork/apply_nested_callback_member_generator.py').read_text()
exec(compile(base, 'apply_nested_callback_member_generator.py', 'exec'))

# Resident DRM sidecar owns the generated CallbackUnpack/CallHostFunction bodies.
bridge_dir = Path('ThunkLibs/libdrm_bridge')
bridge_dir.mkdir(exist_ok=True)
bridge_guest = bridge_dir / 'Guest.cpp'
bridge_guest.write_text(r'''#include "common/Guest.h"
#include "thunkgen_guest_libdrm_bridge.inl"
''')
subprocess.run(['git', 'add', '-N', str(bridge_guest)], check=True)

cmake = Path('ThunkLibs/GuestLibs/CMakeLists.txt')
s = cmake.read_text()
old = 'find_package(PkgConfig REQUIRED)\n'
new = 'find_package(PkgConfig REQUIRED)\nfind_package(Python3 COMPONENTS Interpreter REQUIRED)\n'
assert s.count(old) == 1, s.count(old)
s = s.replace(old, new, 1)

old = '''  generate(libdrm ${CMAKE_CURRENT_SOURCE_DIR}/../libdrm/libdrm_interface.cpp)\n  target_include_directories_from_pkgconfig(libdrm-guest-deps libdrm /usr/include/drm /usr/include/libdrm)\n  add_guest_lib(drm "libdrm.so.2")\n'''
new = '''  generate(libdrm ${CMAKE_CURRENT_SOURCE_DIR}/../libdrm/libdrm_interface.cpp)\n  target_include_directories_from_pkgconfig(libdrm-guest-deps libdrm /usr/include/drm /usr/include/libdrm)\n\n  # Derive the resident DRM callback signature set directly from the normal\n  # generated guest thunk output. callback_member signatures therefore enter\n  # the same by-type resident bridge path as ordinary generated callbacks.\n  set(DRM_GUEST_INL "${CMAKE_CURRENT_BINARY_DIR}/gen/thunkgen_guest_libdrm.inl")\n  set(DRM_BRIDGE_INL "${CMAKE_CURRENT_BINARY_DIR}/gen/thunkgen_guest_libdrm_bridge.inl")\n  set(DRM_BRIDGE_ACCESSORS "${CMAKE_CURRENT_BINARY_DIR}/gen/thunkgen_guest_libdrm_bridge_accessors.inl")\n  add_custom_command(\n    OUTPUT "${DRM_BRIDGE_INL}" "${DRM_BRIDGE_ACCESSORS}"\n    DEPENDS "${DRM_GUEST_INL}" "${FEX_PROJECT_SOURCE_DIR}/LinuxFieldwork/extract_guest_bridge.py"\n    COMMAND "${Python3_EXECUTABLE}"\n      "${FEX_PROJECT_SOURCE_DIR}/LinuxFieldwork/extract_guest_bridge.py"\n      "${DRM_GUEST_INL}" "${DRM_BRIDGE_INL}" "${DRM_BRIDGE_ACCESSORS}" --prefix libdrm\n    VERBATIM)\n  add_custom_target(drm-bridge-generated DEPENDS "${DRM_BRIDGE_INL}" "${DRM_BRIDGE_ACCESSORS}")\n\n  add_library(libdrm_bridge-guest-deps INTERFACE)\n  target_include_directories(libdrm_bridge-guest-deps INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/../include")\n  set(GEN_libdrm_bridge "${DRM_BRIDGE_INL}")\n  add_guest_lib(drm_bridge "libfex-drm-bridge.so")\n  set_target_properties(drm_bridge-guest PROPERTIES OUTPUT_NAME "fex-drm-bridge")\n  target_link_options(drm_bridge-guest PRIVATE "LINKER:-z,nodelete")\n  add_dependencies(drm_bridge-guest drm-bridge-generated)\n\n  add_guest_lib(drm "libdrm.so.2")\n  target_link_libraries(drm-guest PRIVATE drm_bridge-guest)\n  add_dependencies(drm-guest drm-bridge-generated)\n'''
assert s.count(old) == 1, s.count(old)
cmake.write_text(s.replace(old, new, 1))

# Redirect every generated callback allocation in the ordinary DRM wrapper to
# the by-signature resident sidecar accessor. This includes callback_member
# allocations emitted by the generator prototype.
guest = Path('ThunkLibs/libdrm/Guest.cpp')
s = guest.read_text()
old = '#include "thunkgen_guest_libdrm.inl"\n'
new = '''#include "thunkgen_guest_libdrm_bridge_accessors.inl"\n#define AllocateHostTrampolineForGuestFunction FEXAllocateResidentHostTrampolineForGuestFunction\n#include "thunkgen_guest_libdrm.inl"\n#undef AllocateHostTrampolineForGuestFunction\n'''
assert s.count(old) == 1, s.count(old)
guest.write_text(s.replace(old, new, 1))

print('nested callback_member + derived resident DRM bridge applied')
