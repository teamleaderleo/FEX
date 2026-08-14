#!/usr/bin/env python3
from pathlib import Path


def replace_one(path: str, old: str, new: str) -> None:
    p = Path(path)
    s = p.read_text()
    count = s.count(old)
    assert count == 1, (path, count, old[:120])
    p.write_text(s.replace(old, new, 1))


# Turn the retained CUDA host-node callback field into generated callback_member
# metadata. The generic callback_member transform has already taught thunkgen
# how to copy the caller-owned struct and finalize the host trampoline.
replace_one(
    'ThunkLibs/libcuda/libcuda_interface.cpp',
    '''template<>\nstruct fex_gen_type<CUDA_HOST_NODE_PARAMS_st> : fexgen::opaque_type {};\n''',
    '''template<>\nstruct fex_gen_type<CUDA_HOST_NODE_PARAMS_st> {};\ntemplate<>\nstruct fex_gen_config<&CUDA_HOST_NODE_PARAMS_st::_0> : fexgen::callback_member {};\n''')

# The wrapper consumes direct thunkgen accessor output. The macro substitution is
# scoped only to the generated guest inl, so every generated callback allocation
# embeds the resident GuestUnpacker at FEX trampoline creation time.
replace_one(
    'ThunkLibs/libcuda/libcuda_Guest.cpp',
    '''#include "thunkgen_guest_libcuda.inl"\n''',
    '''#include "thunkgen_bridge_accessors_libcuda.inl"\n\n#define AllocateHostTrampolineForGuestFunction FEXAllocateResidentHostTrampolineForGuestFunction\n#include "thunkgen_guest_libcuda.inl"\n#undef AllocateHostTrampolineForGuestFunction\n''')
replace_one(
    'ThunkLibs/libcuda/libcuda_Guest.cpp',
    '''#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(GetCallerForHostFunction(name));\n''',
    '''#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(FEXGetResidentCallerForHostFunction(name));\n''')

bridge = Path('ThunkLibs/libcuda_bridge')
bridge.mkdir(exist_ok=True)
(bridge / 'Guest.cpp').write_text('''// SPDX-License-Identifier: MIT\n// Linux Fieldwork diagnostic: direct role-aware resident CUDA bridge.\n#include "common/Guest.h"\n#include "cuda_defines.h"\n#include "thunkgen_bridge_libcuda.inl"\n''')

replace_one(
    'ThunkLibs/GuestLibs/CMakeLists.txt',
    '''generate(libcuda ${CMAKE_CURRENT_SOURCE_DIR}/../libcuda/libcuda_interface.cpp)\nadd_guest_lib(cuda "libcuda.so.1")''',
    '''generate(libcuda ${CMAKE_CURRENT_SOURCE_DIR}/../libcuda/libcuda_interface.cpp)\n\n# Linux Fieldwork diagnostic: the ordinary CUDA wrapper is unloadable; only\n# generated callers/unpackers live in the process-resident companion.\nadd_library(libcuda_bridge-guest-deps INTERFACE)\ntarget_include_directories(libcuda_bridge-guest-deps INTERFACE\n  "${CMAKE_CURRENT_SOURCE_DIR}/../include"\n  "${CMAKE_CURRENT_SOURCE_DIR}/../libcuda")\nadd_guest_lib(cuda_bridge "libfex-cuda-bridge.so")\nset_target_properties(cuda_bridge-guest PROPERTIES OUTPUT_NAME "fex-cuda-bridge")\nif (TARGET_TYPE STREQUAL "SHARED")\n  target_link_options(cuda_bridge-guest PRIVATE "LINKER:-z,nodelete")\nendif()\nadd_dependencies(cuda_bridge-guest libcuda-guest-bridge-gen)\n\nadd_guest_lib(cuda "libcuda.so.1")\ntarget_link_libraries(cuda-guest PRIVATE cuda_bridge-guest)\nadd_dependencies(cuda-guest libcuda-guest-bridge-gen)''')

print('Applied direct role-aware CUDA resident bridge integration')
