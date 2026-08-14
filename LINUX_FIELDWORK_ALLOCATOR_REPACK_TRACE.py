#!/usr/bin/env python3
from pathlib import Path

path = Path("ThunkLibs/libvulkan/Host.cpp")
text = path.read_text()
old = '''void fex_custom_repack_entry(host_layout<VkAllocationCallbacks>& into, const guest_layout<VkAllocationCallbacks>& from) {
  into.data.pUserData = const_cast<void*>(from.data.pUserData.force_get_host_pointer());
'''
new = '''void fex_custom_repack_entry(host_layout<VkAllocationCallbacks>& into, const guest_layout<VkAllocationCallbacks>& from) {
  fprintf(stderr, "REPACK_ENTER guest_user=%p guest_alloc=0x%llx guest_realloc=0x%llx guest_free=0x%llx guest_internal_alloc=0x%llx guest_internal_free=0x%llx\\n",
          from.data.pUserData.force_get_host_pointer(),
          static_cast<unsigned long long>(from.data.pfnAllocation.data),
          static_cast<unsigned long long>(from.data.pfnReallocation.data),
          static_cast<unsigned long long>(from.data.pfnFree.data),
          static_cast<unsigned long long>(from.data.pfnInternalAllocation.data),
          static_cast<unsigned long long>(from.data.pfnInternalFree.data));
  fflush(stderr);
  into.data.pUserData = const_cast<void*>(from.data.pUserData.force_get_host_pointer());
'''
if text.count(old) != 1:
    raise SystemExit(f"expected one allocator repack entry anchor, found {text.count(old)}")
text = text.replace(old, new, 1)
old2 = '''  if (from.data.pfnInternalFree.data) {
    MakeHostTrampolineForGuestFunctionAt(from.data.pfnInternalFree.data, allocator_guest_unpackers.InternalFree, &into.data.pfnInternalFree);
  } else {
    into.data.pfnInternalFree = nullptr;
  }
}
'''
new2 = '''  if (from.data.pfnInternalFree.data) {
    MakeHostTrampolineForGuestFunctionAt(from.data.pfnInternalFree.data, allocator_guest_unpackers.InternalFree, &into.data.pfnInternalFree);
  } else {
    into.data.pfnInternalFree = nullptr;
  }
  fprintf(stderr, "REPACK_EXIT host_user=%p host_alloc=%p host_realloc=%p host_free=%p host_internal_alloc=%p host_internal_free=%p\\n",
          into.data.pUserData,
          reinterpret_cast<void*>(into.data.pfnAllocation),
          reinterpret_cast<void*>(into.data.pfnReallocation),
          reinterpret_cast<void*>(into.data.pfnFree),
          reinterpret_cast<void*>(into.data.pfnInternalAllocation),
          reinterpret_cast<void*>(into.data.pfnInternalFree));
  fflush(stderr);
}
'''
if text.count(old2) != 1:
    raise SystemExit(f"expected one allocator repack exit anchor, found {text.count(old2)}")
path.write_text(text.replace(old2, new2, 1))
