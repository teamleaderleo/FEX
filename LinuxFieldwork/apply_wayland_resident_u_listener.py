#!/usr/bin/env python3
from pathlib import Path

# Narrow discriminator: move only the Wayland event signature "u" callback
# unpacker into a NODELETE sidecar. Other listener signatures remain stock.
guest = Path('ThunkLibs/libwayland-client/Guest.cpp')
s = guest.read_text()

include_anchor = '#include "thunkgen_guest_libwayland-client.inl"\n'
include_replacement = include_anchor + '\nextern "C" uint64_t FEXWaylandAllocateResidentUListener(void (*callback)());\n'
assert s.count(include_anchor) == 1, s.count(include_anchor)
s = s.replace(include_anchor, include_replacement, 1)

old = '''    } else if (signature == "u") {\n      // E.g. wl_registry::global_remove\n      host_callbacks[i] = WaylandAllocateHostTrampolineForGuestListener<'u'>(callback[i]);\n'''
new = '''    } else if (signature == "u") {\n      // Linux Fieldwork diagnostic: the unpacker for this escaped callback is\n      // process-resident even if libwayland-client-guest.so is reclaimed.\n      host_callbacks[i] = FEXWaylandAllocateResidentUListener(callback[i]);\n'''
assert s.count(old) == 1, s.count(old)
guest.write_text(s.replace(old, new, 1))

bridge_dir = Path('ThunkLibs/libwayland-client_bridge')
bridge_dir.mkdir(exist_ok=True)
(bridge_dir / 'Guest.cpp').write_text(r'''// SPDX-License-Identifier: MIT
// Linux Fieldwork diagnostic: one process-resident Wayland listener unpacker.
#include <wayland-client.h>
#include <wayland-util.h>
#include "common/Guest.h"

extern "C" uint64_t FEXWaylandAllocateResidentUListener(void (*callback)()) {
  using cb = void(void*, wl_proxy*, uint32_t);
  return static_cast<uint64_t>(reinterpret_cast<uintptr_t>(
    reinterpret_cast<void*>(AllocateHostTrampolineForGuestFunction((cb*)callback))));
}
''')

cmake = Path('ThunkLibs/GuestLibs/CMakeLists.txt')
c = cmake.read_text()
anchor = '''generate(libwayland-client ${CMAKE_CURRENT_SOURCE_DIR}/../libwayland-client/libwayland-client_interface.cpp)\nadd_guest_lib(wayland-client "libwayland-client.so.0.20.0")\ntarget_include_directories_from_pkgconfig(libwayland-client-guest-deps wayland-client /usr/include/wayland)\n'''
replacement = '''generate(libwayland-client ${CMAKE_CURRENT_SOURCE_DIR}/../libwayland-client/libwayland-client_interface.cpp)\ntarget_include_directories_from_pkgconfig(libwayland-client-guest-deps wayland-client /usr/include/wayland)\n\nadd_library(libwayland-client_bridge-guest-deps INTERFACE)\ntarget_link_libraries(libwayland-client_bridge-guest-deps INTERFACE libwayland-client-guest-deps)\nadd_guest_lib(wayland-client_bridge "libfex-wayland-client-bridge.so")\nset_target_properties(wayland-client_bridge-guest PROPERTIES OUTPUT_NAME "fex-wayland-client-bridge")\nif (TARGET_TYPE STREQUAL "SHARED")\n  target_link_options(wayland-client_bridge-guest PRIVATE "LINKER:-z,nodelete")\nendif()\n\nadd_guest_lib(wayland-client "libwayland-client.so.0.20.0")\ntarget_link_libraries(wayland-client-guest PRIVATE wayland-client_bridge-guest)\n'''
assert c.count(anchor) == 1, c.count(anchor)
cmake.write_text(c.replace(anchor, replacement, 1))

print('Wayland resident u-listener diagnostic applied')
