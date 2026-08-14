#!/usr/bin/env python3
from pathlib import Path

cmake = Path('ThunkLibs/GuestLibs/CMakeLists.txt')
s = cmake.read_text()
s = s.replace('find_package(PkgConfig REQUIRED)\n', 'find_package(PkgConfig REQUIRED)\nfind_package(Python3 COMPONENTS Interpreter REQUIRED)\n', 1)
old = '''generate(libcuda ${CMAKE_CURRENT_SOURCE_DIR}/../libcuda/libcuda_interface.cpp)\nadd_guest_lib(cuda "libcuda.so.1")\n'''
new = '''generate(libcuda ${CMAKE_CURRENT_SOURCE_DIR}/../libcuda/libcuda_interface.cpp)\n\n# Linux Fieldwork diagnostic: derive a process-resident CUDA bridge from the\n# normal generated indirect/callback signature set.\nset(CUDA_GUEST_INL "${CMAKE_CURRENT_BINARY_DIR}/gen/thunkgen_guest_libcuda.inl")\nset(CUDA_BRIDGE_INL "${CMAKE_CURRENT_BINARY_DIR}/gen/thunkgen_guest_libcuda_bridge.inl")\nset(CUDA_BRIDGE_ACCESSORS "${CMAKE_CURRENT_BINARY_DIR}/gen/thunkgen_guest_libcuda_bridge_accessors.inl")\nadd_custom_command(\n  OUTPUT "${CUDA_BRIDGE_INL}" "${CUDA_BRIDGE_ACCESSORS}"\n  DEPENDS "${CUDA_GUEST_INL}" "${FEX_PROJECT_SOURCE_DIR}/LinuxFieldwork/extract_guest_bridge.py"\n  COMMAND "${Python3_EXECUTABLE}"\n    "${FEX_PROJECT_SOURCE_DIR}/LinuxFieldwork/extract_guest_bridge.py"\n    "${CUDA_GUEST_INL}" "${CUDA_BRIDGE_INL}" "${CUDA_BRIDGE_ACCESSORS}" --prefix libcuda\n  VERBATIM)\nadd_custom_target(cuda-bridge-generated DEPENDS "${CUDA_BRIDGE_INL}" "${CUDA_BRIDGE_ACCESSORS}")\n\nadd_library(libcuda_bridge-guest-deps INTERFACE)\ntarget_include_directories(libcuda_bridge-guest-deps INTERFACE\n  "${CMAKE_CURRENT_SOURCE_DIR}/../include"\n  "${CMAKE_CURRENT_SOURCE_DIR}/../libcuda")\nset(GEN_libcuda_bridge "${CUDA_BRIDGE_INL}" "${CUDA_BRIDGE_ACCESSORS}")\nadd_guest_lib(cuda_bridge "libfex-cuda-bridge.so")\nset_target_properties(cuda_bridge-guest PROPERTIES OUTPUT_NAME "fex-cuda-bridge")\nif (TARGET_TYPE STREQUAL "SHARED")\n  target_link_options(cuda_bridge-guest PRIVATE "LINKER:-z,nodelete")\nendif()\nadd_dependencies(cuda_bridge-guest cuda-bridge-generated)\n\nadd_guest_lib(cuda "libcuda.so.1")\ntarget_link_libraries(cuda-guest PRIVATE cuda_bridge-guest)\nadd_dependencies(cuda-guest cuda-bridge-generated)\n'''
assert s.count(old) == 1, s.count(old)
cmake.write_text(s.replace(old, new, 1))

bridge_dir = Path('ThunkLibs/libcuda_bridge')
bridge_dir.mkdir(exist_ok=True)
(bridge_dir / 'Guest.cpp').write_text('''// Linux Fieldwork diagnostic resident CUDA bridge.\n#include "common/Guest.h"\n#include "cuda_defines.h"\n#include "thunkgen_guest_libcuda_bridge.inl"\n''')

guest = Path('ThunkLibs/libcuda/libcuda_Guest.cpp')
g = guest.read_text()
old_include = '#include "thunkgen_guest_libcuda.inl"\n'
new_include = '''#include "thunkgen_guest_libcuda_bridge_accessors.inl"\n\n#define AllocateHostTrampolineForGuestFunction FEXAllocateResidentHostTrampolineForGuestFunction\n#include "thunkgen_guest_libcuda.inl"\n#undef AllocateHostTrampolineForGuestFunction\n'''
assert g.count(old_include) == 1, g.count(old_include)
g = g.replace(old_include, new_include, 1)
old_pair = '#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(GetCallerForHostFunction(name));'
new_pair = '#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(FEXGetResidentCallerForHostFunction(name));'
assert g.count(old_pair) == 1, g.count(old_pair)
guest.write_text(g.replace(old_pair, new_pair, 1))

print('CUDA derived resident bridge diagnostic applied')
