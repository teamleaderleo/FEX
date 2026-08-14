// SPDX-License-Identifier: MIT
#include <wayland-client.h>
#include <wayland-util.h>

#include <cstdio>
#include <cstdlib>
#include <string_view>

#include "common/Guest.h"
#include "thunkgen_guest_libwayland-client_bridge.inl"

// See wayland-util.h for documentation on protocol message signatures.
// This dispatcher is resident because the native wl_proxy retains the host
// trampoline, whose GuestUnpacker executable address must outlive the wrapper DSO.
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
  return (uint64_t)(uintptr_t)(void*)AllocateHostTrampolineForGuestFunction((cb*)callback);
}

extern "C" uint64_t FEXWaylandResidentAllocateHostTrampoline(const char* raw, void (*callback)()) {
  const std::string_view signature {raw};
  if (signature == "") return Allocate<>(callback); // xdg_toplevel::close
  if (signature == "a") return Allocate<'a'>(callback); // xdg_toplevel::wm_capabilities
  if (signature == "f") return Allocate<'f'>(callback);
  if (signature == "hu") return Allocate<'h', 'u'>(callback); // zwp_linux_dmabuf_feedback_v1::format_table
  if (signature == "i") return Allocate<'i'>(callback); // wl_output_listener::scale
  if (signature == "if") return Allocate<'i', 'f'>(callback); // wl_touch_listener::orientation
  if (signature == "iff") return Allocate<'i', 'f', 'f'>(callback); // wl_touch_listener::shape
  if (signature == "ii") return Allocate<'i', 'i'>(callback); // xdg_toplevel::configure_bounds
  if (signature == "iu") return Allocate<'i', 'u'>(callback);
  if (signature == "iia") return Allocate<'i', 'i', 'a'>(callback); // xdg_toplevel::configure
  if (signature == "iiii") return Allocate<'i', 'i', 'i', 'i'>(callback);
  if (signature == "iiiiissi") return Allocate<'i', 'i', 'i', 'i', 'i', 's', 's', 'i'>(callback); // wl_output_listener::geometry
  if (signature == "n") return Allocate<'n'>(callback); // wl_data_device_listener::data_offer
  if (signature == "o") return Allocate<'o'>(callback); // wl_data_device_listener::selection
  if (signature == "u") return Allocate<'u'>(callback); // wl_registry::global_remove
  if (signature == "uff") return Allocate<'u', 'f', 'f'>(callback); // wl_pointer_listener::motion
  if (signature == "uffff") return Allocate<'u', 'f', 'f', 'f', 'f'>(callback);
  if (signature == "uhu") return Allocate<'u', 'h', 'u'>(callback); // wl_keyboard_listener::keymap
  if (signature == "ui") return Allocate<'u', 'i'>(callback); // wl_pointer_listener::axis_discrete
  if (signature == "uiff") return Allocate<'u', 'i', 'f', 'f'>(callback); // wl_touch_listener::motion
  if (signature == "uiii") return Allocate<'u', 'i', 'i', 'i'>(callback); // wl_output_listener::mode
  if (signature == "uiiii") return Allocate<'u', 'i', 'i', 'i', 'i'>(callback);
  if (signature == "uo") return Allocate<'u', 'o'>(callback); // wl_pointer_listener::leave
  if (signature == "uoa") return Allocate<'u', 'o', 'a'>(callback); // wl_keyboard_listener::enter
  if (signature == "uoff") return Allocate<'u', 'o', 'f', 'f'>(callback); // wl_pointer_listener::enter
  if (signature == "uoffo") return Allocate<'u', 'o', 'f', 'f', 'o'>(callback); // wl_data_device_listener::enter
  if (signature == "uoo") return Allocate<'u', 'o', 'o'>(callback);
  if (signature == "us") return Allocate<'u', 's'>(callback);
  if (signature == "uss") return Allocate<'u', 's', 's'>(callback);
  if (signature == "usu") return Allocate<'u', 's', 'u'>(callback); // wl_registry::global
  if (signature == "uu") return Allocate<'u', 'u'>(callback); // wl_pointer_listener::axis_stop
  if (signature == "uuf") return Allocate<'u', 'u', 'f'>(callback); // wl_pointer_listener::axis
  if (signature == "uui") return Allocate<'u', 'u', 'i'>(callback); // wl_touch_listener::up
  if (signature == "uuoiff") return Allocate<'u', 'u', 'o', 'i', 'f', 'f'>(callback); // wl_touch_listener::down
  if (signature == "uuou") return Allocate<'u', 'u', 'o', 'u'>(callback);
  if (signature == "uuu") return Allocate<'u', 'u', 'u'>(callback); // zwp_linux_dmabuf_v1::modifier
  if (signature == "uuuu") return Allocate<'u', 'u', 'u', 'u'>(callback); // wl_pointer_listener::button
  if (signature == "uuuuu") return Allocate<'u', 'u', 'u', 'u', 'u'>(callback); // wl_keyboard_listener::modifiers
  if (signature == "s") return Allocate<'s'>(callback); // wl_seat::name
  if (signature == "ss") return Allocate<'s', 's'>(callback);
  if (signature == "sii") return Allocate<'s', 'i', 'i'>(callback); // zwp_text_input_v3::preedit_string
  fprintf(stderr, "TODO: Unknown wayland event signature descriptor %s\n", raw);
  std::abort();
}
