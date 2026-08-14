#!/usr/bin/env python3
from pathlib import Path
import re

# Generalize the proven one-signature Wayland resident-listener split across the
# existing 64-bit protocol signature set. The unloadable wrapper still owns
# listener-table storage and protocol parsing; the NODELETE companion owns the
# guest callback unpackers embedded in FEX host trampolines.

guest = Path('ThunkLibs/libwayland-client/Guest.cpp')
s = guest.read_text()

include_anchor = '#include "thunkgen_guest_libwayland-client.inl"\n'
include_replacement = include_anchor + '\nextern "C" uint64_t FEXWaylandAllocateResidentListener(void (*callback)(), const char* signature);\n'
assert s.count(include_anchor) == 1, s.count(include_anchor)
s = s.replace(include_anchor, include_replacement, 1)

pat = re.compile(r'host_callbacks\[i\] = WaylandAllocateHostTrampolineForGuestListener<[^>]*>\(callback\[i\]\);')
count = len(pat.findall(s))
assert count >= 35, count
s = pat.sub('host_callbacks[i] = FEXWaylandAllocateResidentListener(callback[i], signature.c_str());', s)
guest.write_text(s)

bridge_dir = Path('ThunkLibs/libwayland-client_bridge')
bridge_dir.mkdir(exist_ok=True)
(bridge_dir / 'Guest.cpp').write_text(r'''// SPDX-License-Identifier: MIT
// Linux Fieldwork diagnostic: process-resident Wayland listener unpackers.
#include <wayland-client.h>
#include <wayland-util.h>
#include "common/Guest.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

template<char>
struct ArgType;
template<> struct ArgType<'s'> { using type = const char*; };
template<> struct ArgType<'u'> { using type = uint32_t; };
template<> struct ArgType<'i'> { using type = int32_t; };
template<> struct ArgType<'o'> { using type = wl_proxy*; };
template<> struct ArgType<'n'> { using type = wl_proxy*; };
template<> struct ArgType<'a'> { using type = wl_array*; };
template<> struct ArgType<'f'> { using type = wl_fixed_t; };
template<> struct ArgType<'h'> { using type = int32_t; };

template<char... Signature>
static uint64_t Allocate(void (*callback)()) {
  using cb = void(void*, wl_proxy*, typename ArgType<Signature>::type...);
  return static_cast<uint64_t>(reinterpret_cast<uintptr_t>(
    reinterpret_cast<void*>(AllocateHostTrampolineForGuestFunction((cb*)callback))));
}

extern "C" uint64_t FEXWaylandAllocateResidentListener(void (*callback)(), const char* signature) {
  if (!std::strcmp(signature, "")) return Allocate<>(callback);
  if (!std::strcmp(signature, "a")) return Allocate<'a'>(callback);
  if (!std::strcmp(signature, "f")) return Allocate<'f'>(callback);
  if (!std::strcmp(signature, "hu")) return Allocate<'h','u'>(callback);
  if (!std::strcmp(signature, "i")) return Allocate<'i'>(callback);
  if (!std::strcmp(signature, "if")) return Allocate<'i','f'>(callback);
  if (!std::strcmp(signature, "iff")) return Allocate<'i','f','f'>(callback);
  if (!std::strcmp(signature, "ii")) return Allocate<'i','i'>(callback);
  if (!std::strcmp(signature, "iu")) return Allocate<'i','u'>(callback);
  if (!std::strcmp(signature, "iia")) return Allocate<'i','i','a'>(callback);
  if (!std::strcmp(signature, "iiii")) return Allocate<'i','i','i','i'>(callback);
  if (!std::strcmp(signature, "iiiiissi")) return Allocate<'i','i','i','i','i','s','s','i'>(callback);
  if (!std::strcmp(signature, "n")) return Allocate<'n'>(callback);
  if (!std::strcmp(signature, "o")) return Allocate<'o'>(callback);
  if (!std::strcmp(signature, "u")) return Allocate<'u'>(callback);
  if (!std::strcmp(signature, "uff")) return Allocate<'u','f','f'>(callback);
  if (!std::strcmp(signature, "uffff")) return Allocate<'u','f','f','f','f'>(callback);
  if (!std::strcmp(signature, "uhu")) return Allocate<'u','h','u'>(callback);
  if (!std::strcmp(signature, "ui")) return Allocate<'u','i'>(callback);
  if (!std::strcmp(signature, "uiff")) return Allocate<'u','i','f','f'>(callback);
  if (!std::strcmp(signature, "uiii")) return Allocate<'u','i','i','i'>(callback);
  if (!std::strcmp(signature, "uiiii")) return Allocate<'u','i','i','i','i'>(callback);
  if (!std::strcmp(signature, "uo")) return Allocate<'u','o'>(callback);
  if (!std::strcmp(signature, "uoa")) return Allocate<'u','o','a'>(callback);
  if (!std::strcmp(signature, "uoff")) return Allocate<'u','o','f','f'>(callback);
  if (!std::strcmp(signature, "uoffo")) return Allocate<'u','o','f','f','o'>(callback);
  if (!std::strcmp(signature, "uoo")) return Allocate<'u','o','o'>(callback);
  if (!std::strcmp(signature, "us")) return Allocate<'u','s'>(callback);
  if (!std::strcmp(signature, "uss")) return Allocate<'u','s','s'>(callback);
  if (!std::strcmp(signature, "usu")) return Allocate<'u','s','u'>(callback);
  if (!std::strcmp(signature, "uu")) return Allocate<'u','u'>(callback);
  if (!std::strcmp(signature, "uuf")) return Allocate<'u','u','f'>(callback);
  if (!std::strcmp(signature, "uui")) return Allocate<'u','u','i'>(callback);
  if (!std::strcmp(signature, "uuoiff")) return Allocate<'u','u','o','i','f','f'>(callback);
  if (!std::strcmp(signature, "uuou")) return Allocate<'u','u','o','u'>(callback);
  if (!std::strcmp(signature, "uuu")) return Allocate<'u','u','u'>(callback);
  if (!std::strcmp(signature, "uuuu")) return Allocate<'u','u','u','u'>(callback);
  if (!std::strcmp(signature, "uuuuu")) return Allocate<'u','u','u','u','u'>(callback);
  if (!std::strcmp(signature, "s")) return Allocate<'s'>(callback);
  if (!std::strcmp(signature, "ss")) return Allocate<'s','s'>(callback);
  if (!std::strcmp(signature, "sii")) return Allocate<'s','i','i'>(callback);

  std::fprintf(stderr, "Unknown resident Wayland listener signature: %s\n", signature);
  std::abort();
}
''')

cmake = Path('ThunkLibs/GuestLibs/CMakeLists.txt')
c = cmake.read_text()
anchor = '''generate(libwayland-client ${CMAKE_CURRENT_SOURCE_DIR}/../libwayland-client/libwayland-client_interface.cpp)\nadd_guest_lib(wayland-client "libwayland-client.so.0.20.0")\ntarget_include_directories_from_pkgconfig(libwayland-client-guest-deps wayland-client /usr/include/wayland)\n'''
replacement = '''generate(libwayland-client ${CMAKE_CURRENT_SOURCE_DIR}/../libwayland-client/libwayland-client_interface.cpp)\ntarget_include_directories_from_pkgconfig(libwayland-client-guest-deps wayland-client /usr/include/wayland)\n\nadd_library(libwayland-client_bridge-guest-deps INTERFACE)\ntarget_link_libraries(libwayland-client_bridge-guest-deps INTERFACE libwayland-client-guest-deps)\nadd_guest_lib(wayland-client_bridge "libfex-wayland-client-bridge.so")\nset_target_properties(wayland-client_bridge-guest PROPERTIES OUTPUT_NAME "fex-wayland-client-bridge")\nif (TARGET_TYPE STREQUAL "SHARED")\n  target_link_options(wayland-client_bridge-guest PRIVATE "LINKER:-z,nodelete")\nendif()\n\nadd_guest_lib(wayland-client "libwayland-client.so.0.20.0")\ntarget_link_libraries(wayland-client-guest PRIVATE wayland-client_bridge-guest)\n'''
assert c.count(anchor) == 1, c.count(anchor)
cmake.write_text(c.replace(anchor, replacement, 1))

print(f'Wayland resident listener diagnostic applied to {count} typed allocation sites')
