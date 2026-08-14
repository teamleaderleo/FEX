#!/usr/bin/env python3
from pathlib import Path

path = Path("ThunkLibs/libvulkan/Host.cpp")
text = path.read_text()
old = '''bool fex_custom_repack_exit(guest_layout<VkAllocationCallbacks>&, const host_layout<VkAllocationCallbacks>&) {
  return false;
}
'''
new = '''bool fex_custom_repack_exit(guest_layout<VkAllocationCallbacks>&, const host_layout<VkAllocationCallbacks>&) {
  // Causal control: VkAllocationCallbacks is input-only at these call sites.
  // Tell repack_wrapper that custom exit handling is complete so it does not
  // automatically write the temporary host representation back to guest memory.
  return true;
}
'''
if text.count(old) != 1:
    raise SystemExit(f"expected one allocator exit hook, found {text.count(old)}")
path.write_text(text.replace(old, new, 1))
