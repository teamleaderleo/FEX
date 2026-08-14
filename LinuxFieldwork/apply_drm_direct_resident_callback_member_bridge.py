#!/usr/bin/env python3
from pathlib import Path


def replace_one(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    assert count == 1, (label, count)
    path.write_text(text.replace(old, new, 1))


interface = Path('ThunkLibs/libdrm/libdrm_interface.cpp')
replace_one(
    interface,
    '''template<>\nstruct fex_gen_type<drmEventContext> : fexgen::assume_compatible_data_layout {};\n''',
    '''// drmHandleEvent callbacks can escape the guest wrapper through native\n// host trampolines, so thunkgen must type them as callbacks and generate\n// resident unpacker accessors.\ntemplate<>\nstruct fex_gen_config<&drmEventContext::vblank_handler> : fexgen::callback_member {};\ntemplate<>\nstruct fex_gen_config<&drmEventContext::page_flip_handler> : fexgen::callback_member {};\ntemplate<>\nstruct fex_gen_config<&drmEventContext::page_flip_handler2> : fexgen::callback_member {};\ntemplate<>\nstruct fex_gen_config<&drmEventContext::sequence_handler> : fexgen::callback_member {};\n''',
    'drmEventContext callback members')

guest = Path('ThunkLibs/libdrm/Guest.cpp')
replace_one(
    guest,
    '#include "thunkgen_guest_libdrm.inl"\n',
    '''#include "thunkgen_bridge_accessors_libdrm.inl"\n\n#define AllocateHostTrampolineForGuestFunction FEXAllocateResidentHostTrampolineForGuestFunction\n#include "thunkgen_guest_libdrm.inl"\n#undef AllocateHostTrampolineForGuestFunction\n''',
    'DRM generated guest include')

bridge = Path('ThunkLibs/libdrm_bridge')
bridge.mkdir(exist_ok=True)
(bridge / 'Guest.cpp').write_text(r'''// SPDX-License-Identifier: MIT
#include <xf86drm.h>

#include "common/Guest.h"
#include "thunkgen_bridge_libdrm.inl"
''')

cmake = Path('ThunkLibs/GuestLibs/CMakeLists.txt')
replace_one(
    cmake,
    '''  generate(libdrm ${CMAKE_CURRENT_SOURCE_DIR}/../libdrm/libdrm_interface.cpp)\n  target_include_directories_from_pkgconfig(libdrm-guest-deps libdrm /usr/include/drm /usr/include/libdrm)\n  add_guest_lib(drm "libdrm.so.2")\n''',
    '''  generate(libdrm ${CMAKE_CURRENT_SOURCE_DIR}/../libdrm/libdrm_interface.cpp)\n  target_include_directories_from_pkgconfig(libdrm-guest-deps libdrm /usr/include/drm /usr/include/libdrm)\n  add_guest_lib(drm "libdrm.so.2")\n  add_guest_bridge(drm_bridge "libfex-drm-bridge.so"\n    OUTPUT_NAME "fex-drm-bridge"\n    WRAPPER_TARGET drm-guest\n    GENERATOR libdrm\n    DEP_TARGETS libdrm-guest-deps)\n''',
    'DRM guest bridge CMake')

print('Applied direct role-aware DRM callback_member resident bridge')
