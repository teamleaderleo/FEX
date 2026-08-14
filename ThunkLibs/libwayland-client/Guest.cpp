/*
$info$
tags: thunklibs|wayland-client
$end_info$
*/

#include <wayland-util.h>
#include <wayland-client.h>

// These must be re-declared with an initializer here, since they don't get exported otherwise
// NOTE: The initializers for these must be fetched from the host Wayland library, however
//       we can't control how these symbols are loaded since they are global const objects.
//       LD puts them in the application rodata section and ignores any nontrivial library-provided
//       initializers. There is a workaround to enable late initialization anyway in OnInit.
// NOTE: We only need to do this for interfaces exported by libwayland-client itself. Interfaces
//       defined by external libraries work fine.
extern "C" const wl_interface wl_output_interface {};
extern "C" const wl_interface wl_shm_pool_interface {};
extern "C" const wl_interface wl_pointer_interface {};
extern "C" const wl_interface wl_compositor_interface {};
extern "C" const wl_interface wl_shm_interface {};
extern "C" const wl_interface wl_registry_interface {};
extern "C" const wl_interface wl_buffer_interface {};
extern "C" const wl_interface wl_seat_interface {};
extern "C" const wl_interface wl_surface_interface {};
extern "C" const wl_interface wl_keyboard_interface {};
extern "C" const wl_interface wl_callback_interface {};
extern "C" const wl_interface wl_display_interface {};
extern "C" const wl_interface wl_data_offer_interface {};
extern "C" const wl_interface wl_data_source_interface {};
extern "C" const wl_interface wl_data_device_interface {};
extern "C" const wl_interface wl_data_device_manager_interface {};
extern "C" const wl_interface wl_shell_interface {};
extern "C" const wl_interface wl_shell_surface_interface {};
extern "C" const wl_interface wl_touch_interface {};
extern "C" const wl_interface wl_region_interface {};
extern "C" const wl_interface wl_subcompositor_interface {};
extern "C" const wl_interface wl_subsurface_interface {};

#include <algorithm>
#include <array>
#include <charconv>
#include <cstdio>
#include <cstdarg>
#include <cstring>
#include <string>

#include "common/Guest.h"

#include "thunkgen_guest_libwayland-client.inl"

extern "C" uint64_t FEXWaylandResidentAllocateHostTrampoline(const char* signature, void (*callback)());

#define WL_CLOSURE_MAX_ARGS 20

extern "C" int wl_proxy_add_listener(wl_proxy* proxy, void (**callback)(void), void* data) {
  // Replace guest-provided callback table with host-callable function pointers
  // NOTE: A reference to this table is stored in the wl_proxy, so the data
  //       must remain valid until the proxy is destroyed (or another listener
  //       is added)
  delete[] (uint64_t*)wl_proxy_get_listener(proxy); // Delete previous substitute, if any
  auto host_callbacks = new uint64_t[WL_CLOSURE_MAX_ARGS];

  for (int i = 0; i < fex_wl_get_interface_event_count(proxy); ++i) {
    char event_signature[16];
    fex_wl_get_interface_event_signature(proxy, i, event_signature);
    auto signature2 = std::string_view {event_signature};

    // A leading number indicates the minimum protocol version
    uint32_t since_version = 0;
    auto [ptr, res] = std::from_chars(signature2.begin(), signature2.end(), since_version, 10);
    auto signature = std::string {signature2.substr(ptr - signature2.begin())};

    // ? just indicates that the argument may be null, so it doesn't change the signature
    signature.erase(std::remove(signature.begin(), signature.end(), '?'), signature.end());

    host_callbacks[i] = FEXWaylandResidentAllocateHostTrampoline(signature.c_str(), callback[i]);
  }

  return fexfn_pack_wl_proxy_add_listener(proxy, (void (**)())host_callbacks, data);
}

extern "C" void wl_proxy_destroy(wl_proxy* proxy) {
  // Delete substitute callback table (if any), then the proxy itself
  delete[] (uint64_t*)wl_proxy_get_listener(proxy);
  fexfn_pack_wl_proxy_destroy(proxy);
}

// Adapted from the Wayland sources
static const char* get_next_argument_type(const char* signature, char& type) {
  for (; *signature; ++signature) {
    switch (*signature) {
    case 'i':
    case 'u':
    case 'f':
    case 's':
    case 'o':
    case 'n':
    case 'a':
    case 'h': type = *signature; return signature + 1;

    default: continue;
    }
  }
  type = 0;
  return signature;
}

static void wl_argument_from_va_list(const char* signature, wl_argument* args, int count, va_list ap) {

  auto sig_iter = signature;
  for (int i = 0; i < count; i++) {
    char arg_type;
    sig_iter = get_next_argument_type(sig_iter, arg_type);

    switch (arg_type) {
    case 'i': args[i].i = va_arg(ap, int32_t); break;
    case 'u': args[i].u = va_arg(ap, uint32_t); break;
    case 'f': args[i].f = va_arg(ap, wl_fixed_t); break;
    case 's': args[i].s = va_arg(ap, const char*); break;
    case 'o': args[i].o = va_arg(ap, struct wl_object*); break;
    case 'n': args[i].o = va_arg(ap, struct wl_object*); break;
    case 'a': args[i].a = va_arg(ap, struct wl_array*); break;
    case 'h': args[i].h = va_arg(ap, int32_t); break;
    case '\0': return;
    }
  }
}

extern "C" void wl_proxy_marshal(wl_proxy* proxy, uint32_t opcode, ...) {
  wl_argument args[WL_CLOSURE_MAX_ARGS];
  va_list ap;

  va_start(ap, opcode);
  // This is equivalent to reading proxy->interface->methods[opcode].signature on 64-bit.
  // On 32-bit, the data layout differs between host and guest however, so we let the host extract the data.
  char signature[64];
  fex_wl_get_method_signature(proxy, opcode, signature);
  wl_argument_from_va_list(signature, args, WL_CLOSURE_MAX_ARGS, ap);
  va_end(ap);

  wl_proxy_marshal_array(proxy, opcode, args);
}

extern "C" wl_proxy* wl_proxy_marshal_constructor(wl_proxy* proxy, uint32_t opcode, const wl_interface* interface, ...) {
  wl_argument args[WL_CLOSURE_MAX_ARGS];
  va_list ap;

  va_start(ap, interface);
  // This is equivalent to reading ((wl_proxy_private*)proxy)->interface->methods[opcode].signature on 64-bit.
  // On 32-bit, the data layout differs between host and guest however, so we let the host extract the data.
  char signature[64];
  fex_wl_get_method_signature(proxy, opcode, signature);
  wl_argument_from_va_list(signature, args, WL_CLOSURE_MAX_ARGS, ap);
  va_end(ap);

  return wl_proxy_marshal_array_constructor(proxy, opcode, args, interface);
}

extern "C" wl_proxy* wl_proxy_marshal_constructor_versioned(wl_proxy* proxy, uint32_t opcode, const wl_interface* interface, uint32_t version, ...) {
  wl_argument args[WL_CLOSURE_MAX_ARGS];
  va_list ap;

  va_start(ap, version);
  // This is equivalent to reading ((wl_proxy_private*)proxy)->interface->methods[opcode].signature on 64-bit.
  // On 32-bit, the data layout differs between host and guest however, so we let the host extract the data.
  char signature[64];
  fex_wl_get_method_signature(proxy, opcode, signature);
  wl_argument_from_va_list(signature, args, WL_CLOSURE_MAX_ARGS, ap);
  va_end(ap);

  return wl_proxy_marshal_array_constructor_versioned(proxy, opcode, args, interface, version);
}

extern "C" wl_proxy* wl_proxy_marshal_flags(wl_proxy* proxy, uint32_t opcode, const wl_interface* interface, uint32_t version, uint32_t flags, ...) {
  wl_argument args[WL_CLOSURE_MAX_ARGS];
  va_list ap;

  va_start(ap, flags);
  // This is equivalent to reading proxy->interface->methods[opcode].signature on 64-bit.
  // On 32-bit, the data layout differs between host and guest however, so we let the host extract the data.
  char signature[64];
  fex_wl_get_method_signature(proxy, opcode, signature);
  wl_argument_from_va_list(signature, args, WL_CLOSURE_MAX_ARGS, ap);
  va_end(ap);

  // wl_proxy_marshal_array_flags is only available starting from Wayland 1.19.91
#if WAYLAND_VERSION_MAJOR * 10000 + WAYLAND_VERSION_MINOR * 100 + WAYLAND_VERSION_MICRO >= 11991
  return wl_proxy_marshal_array_flags(proxy, opcode, interface, version, flags, args);
#else
  fprintf(stderr, "Host Wayland version is too old to support FEX thunking\n");
  __builtin_trap();
#endif
}

extern "C" void wl_log_set_handler_client(wl_log_func_t handler) {
  // Ignore
}


void OnInit() {
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_output_interface), "wl_output_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_shm_pool_interface), "wl_shm_pool_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_pointer_interface), "wl_pointer_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_compositor_interface), "wl_compositor_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_shm_interface), "wl_shm_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_registry_interface), "wl_registry_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_buffer_interface), "wl_buffer_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_seat_interface), "wl_seat_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_surface_interface), "wl_surface_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_keyboard_interface), "wl_keyboard_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_callback_interface), "wl_callback_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_display_interface), "wl_display_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_data_offer_interface), "wl_data_offer_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_data_source_interface), "wl_data_source_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_data_device_interface), "wl_data_device_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_data_device_manager_interface), "wl_data_device_manager_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_shell_interface), "wl_shell_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_shell_surface_interface), "wl_shell_surface_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_touch_interface), "wl_touch_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_region_interface), "wl_region_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_subcompositor_interface), "wl_subcompositor_interface");
  fex_wl_exchange_interface_pointer(const_cast<wl_interface*>(&wl_subsurface_interface), "wl_subsurface_interface");
}

// Would insert spaces around -
// clang-format off
LOAD_LIB_INIT(libwayland-client, OnInit)
