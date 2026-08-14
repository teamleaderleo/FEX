from pathlib import Path

path = Path("ThunkLibs/libvulkan/Host.cpp")
text = path.read_text()

anchor = '''void fex_custom_repack_entry(host_layout<VkAllocationCallbacks>& into, const guest_layout<VkAllocationCallbacks>& from) {
  into.data.pUserData = const_cast<void*>(from.data.pUserData.force_get_host_pointer());
'''
replacement = r'''static uint64_t allocator_repack_sequence;

void fex_custom_repack_entry(host_layout<VkAllocationCallbacks>& into, const guest_layout<VkAllocationCallbacks>& from) {
  const uint64_t repack_sequence = ++allocator_repack_sequence;
  into.data.pUserData = const_cast<void*>(from.data.pUserData.force_get_host_pointer());
  fprintf(stderr,
          "ALLOC_REPACK_BEGIN seq=%lu guest_user=%p host_user=%p guest_alloc=%p guest_realloc=%p guest_free=%p guest_internal_alloc=%p guest_internal_free=%p\n",
          repack_sequence,
          (void*)from.data.pUserData.data,
          into.data.pUserData,
          (void*)from.data.pfnAllocation.data,
          (void*)from.data.pfnReallocation.data,
          (void*)from.data.pfnFree.data,
          (void*)from.data.pfnInternalAllocation.data,
          (void*)from.data.pfnInternalFree.data);
  fflush(stderr);
'''
if text.count(anchor) != 1:
    raise SystemExit(f"expected one allocator repack entry anchor, found {text.count(anchor)}")
text = text.replace(anchor, replacement, 1)

end_anchor = '''  if (from.data.pfnInternalFree.data) {
    MakeHostTrampolineForGuestFunctionAt(from.data.pfnInternalFree.data, allocator_guest_unpackers.InternalFree, &into.data.pfnInternalFree);
  } else {
    into.data.pfnInternalFree = nullptr;
  }
}
'''
end_replacement = r'''  if (from.data.pfnInternalFree.data) {
    MakeHostTrampolineForGuestFunctionAt(from.data.pfnInternalFree.data, allocator_guest_unpackers.InternalFree, &into.data.pfnInternalFree);
  } else {
    into.data.pfnInternalFree = nullptr;
  }
  fprintf(stderr,
          "ALLOC_REPACK_END seq=%lu host_user=%p host_alloc=%p host_realloc=%p host_free=%p host_internal_alloc=%p host_internal_free=%p\n",
          repack_sequence,
          into.data.pUserData,
          (void*)into.data.pfnAllocation,
          (void*)into.data.pfnReallocation,
          (void*)into.data.pfnFree,
          (void*)into.data.pfnInternalAllocation,
          (void*)into.data.pfnInternalFree);
  fflush(stderr);
}
'''
if text.count(end_anchor) != 1:
    raise SystemExit(f"expected one allocator repack exit anchor, found {text.count(end_anchor)}")
path.write_text(text.replace(end_anchor, end_replacement, 1))
