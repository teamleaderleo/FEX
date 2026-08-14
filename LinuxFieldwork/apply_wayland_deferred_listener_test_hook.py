#!/usr/bin/env python3
from pathlib import Path

# Test-only hook used by both A/B variants. It keeps the real FEX Wayland
# listener allocation/finalization path but substitutes a deterministic native
# consumer: the finalized listener is invoked once before wrapper close and
# once later, after the guest test has closed the wrapper.
host = Path('ThunkLibs/libwayland-client/Host.cpp')
h = host.read_text()
include_anchor = '#include <string>\n'
assert h.count(include_anchor) == 1, h.count(include_anchor)
h = h.replace(include_anchor, include_anchor + '#include <thread>\n#include <chrono>\n', 1)

old = '''  // Pass the original function pointer table to the host wayland library. This ensures the table is valid until the listener is unregistered.\n  return fexldr_ptr_libwayland_client_wl_proxy_add_listener(proxy, callback_table, data);\n}\n'''
new = '''  // Linux Fieldwork diagnostic: retain the finalized host-callable listener\n  // exactly as native Wayland would, then invoke it twice from a native thread.\n  // The first callback is a pre-close control; the second fires after the guest\n  // test has closed its wrapper.\n  using TestCallback = void (*)(void*, wl_proxy*, uint32_t);\n  auto test_callback = reinterpret_cast<TestCallback>(callback_table[0]);\n  std::thread {[test_callback, data, proxy]() {\n    std::this_thread::sleep_for(std::chrono::milliseconds(100));\n    test_callback(data, proxy, 41);\n    std::this_thread::sleep_for(std::chrono::milliseconds(500));\n    test_callback(data, proxy, 42);\n  }}.detach();\n  return 0;\n}\n'''
assert h.count(old) == 1, h.count(old)
host.write_text(h.replace(old, new, 1))

guest = Path('ThunkLibs/libwayland-client/Guest.cpp')
g = guest.read_text()
old_delete = '  delete[] (uint64_t*)wl_proxy_get_listener(proxy); // Delete previous substitute, if any\n'
new_delete = '  // Linux Fieldwork diagnostic fake proxy has no native listener query.\n'
assert g.count(old_delete) == 1, g.count(old_delete)
guest.write_text(g.replace(old_delete, new_delete, 1))

print('Wayland deferred listener test hook applied')
