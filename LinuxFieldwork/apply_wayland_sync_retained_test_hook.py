#!/usr/bin/env python3
from pathlib import Path

interface = Path('ThunkLibs/libwayland-client/libwayland-client_interface.cpp')
i = interface.read_text()
append = '''

// Linux Fieldwork diagnostic: synchronously invoke the host-retained listener.
void fex_wl_test_trigger(uint32_t value);
template<>
struct fex_gen_config<fex_wl_test_trigger> : fexgen::custom_host_impl {};
'''
assert 'fex_wl_test_trigger' not in i
i += append
interface.write_text(i)

guest = Path('ThunkLibs/libwayland-client/Guest.cpp')
g = guest.read_text()
old_delete = '  delete[] (uint64_t*)wl_proxy_get_listener(proxy); // Delete previous substitute, if any\n'
new_delete = '  // Linux Fieldwork diagnostic fake proxy has no native listener query.\n'
assert g.count(old_delete) == 1, g.count(old_delete)
g = g.replace(old_delete, new_delete, 1)
include_anchor = '#include "thunkgen_guest_libwayland-client.inl"\n'
export = '''#include "thunkgen_guest_libwayland-client.inl"

extern "C" __attribute__((visibility("default"))) void fex_wayland_test_trigger(uint32_t value) {
  fex_wl_test_trigger(value);
}
'''
assert g.count(include_anchor) == 1, g.count(include_anchor)
g = g.replace(include_anchor, export, 1)
guest.write_text(g)

host = Path('ThunkLibs/libwayland-client/Host.cpp')
h = host.read_text()
fn_anchor = '''extern "C" int
fexfn_impl_libwayland_client_wl_proxy_add_listener(struct wl_proxy* proxy, guest_layout<void (**)(void)> callback_table_raw, void* data) {
'''
state = '''using FEXWaylandTestCallback = void (*)(void*, wl_proxy*, uint32_t);
static FEXWaylandTestCallback fex_wayland_retained_test_callback {};
static void* fex_wayland_retained_test_data {};
static wl_proxy* fex_wayland_retained_test_proxy {};

extern "C" int
fexfn_impl_libwayland_client_wl_proxy_add_listener(struct wl_proxy* proxy, guest_layout<void (**)(void)> callback_table_raw, void* data) {
'''
assert h.count(fn_anchor) == 1, h.count(fn_anchor)
h = h.replace(fn_anchor, state, 1)
old_return = '''  // Pass the original function pointer table to the host wayland library. This ensures the table is valid until the listener is unregistered.
  return fexldr_ptr_libwayland_client_wl_proxy_add_listener(proxy, callback_table, data);
}
'''
new_return = '''  fex_wayland_retained_test_callback = reinterpret_cast<FEXWaylandTestCallback>(callback_table[0]);
  fex_wayland_retained_test_data = data;
  fex_wayland_retained_test_proxy = proxy;
  fprintf(stderr, "WAYLAND_HOST_RETAIN trampoline=%p data=%p proxy=%p\\n",
          reinterpret_cast<void*>(fex_wayland_retained_test_callback), data, proxy);
  fflush(stderr);
  return 0;
}

extern "C" void fexfn_impl_libwayland_client_fex_wl_test_trigger(uint32_t value) {
  fprintf(stderr, "WAYLAND_HOST_TRIGGER value=%u trampoline=%p\\n", value,
          reinterpret_cast<void*>(fex_wayland_retained_test_callback));
  fflush(stderr);
  if (!fex_wayland_retained_test_callback) std::abort();
  fex_wayland_retained_test_callback(fex_wayland_retained_test_data, fex_wayland_retained_test_proxy, value);
  fprintf(stderr, "WAYLAND_HOST_TRIGGER_RETURN value=%u\\n", value);
  fflush(stderr);
}
'''
assert h.count(old_return) == 1, h.count(old_return)
h = h.replace(old_return, new_return, 1)
host.write_text(h)

print('Wayland synchronous retained-listener test hook applied')
