from pathlib import Path

iface_path = Path("ThunkLibs/libvulkan/libvulkan_interface.cpp")
iface = iface_path.read_text()
old = '''// TODO: Should not be opaque, but it's usually NULL anyway. Supporting the contained function pointers will need more work.
template<>
struct fex_gen_type<VkAllocationCallbacks> : fexgen::opaque_type {};'''
new = '''// Linux Fieldwork experiment: expose VkAllocationCallbacks to the normal
// struct repacker and handle its nested function-pointer members explicitly.
template<>
struct fex_gen_type<VkAllocationCallbacks> : fexgen::emit_layout_wrappers {};
template<>
struct fex_gen_config<&VkAllocationCallbacks::pfnAllocation> : fexgen::custom_repack {};
template<>
struct fex_gen_config<&VkAllocationCallbacks::pfnReallocation> : fexgen::custom_repack {};
template<>
struct fex_gen_config<&VkAllocationCallbacks::pfnFree> : fexgen::custom_repack {};
template<>
struct fex_gen_config<&VkAllocationCallbacks::pfnInternalAllocation> : fexgen::custom_repack {};
template<>
struct fex_gen_config<&VkAllocationCallbacks::pfnInternalFree> : fexgen::custom_repack {};'''
if iface.count(old) != 1:
    raise SystemExit(f"expected one VkAllocationCallbacks opaque annotation, found {iface.count(old)}")
iface_path.write_text(iface.replace(old, new, 1))

host_path = Path("ThunkLibs/libvulkan/Host.cpp")
host = host_path.read_text()
anchor = '#include "thunkgen_host_libvulkan.inl"\n'
if host.count(anchor) != 1:
    raise SystemExit(f"expected one generated-host include, found {host.count(anchor)}")
insert = r'''

[[noreturn]] static void AllocatorCallbackStubFatal(const char* callback) {
  fprintf(stderr, "FEX_ALLOCATOR_STUB callback=%s\n", callback);
  fflush(stderr);
  std::abort();
}

static VKAPI_ATTR void* VKAPI_CALL AllocatorAllocationStub(void*, size_t, size_t, VkSystemAllocationScope) {
  AllocatorCallbackStubFatal("pfnAllocation");
}

static VKAPI_ATTR void* VKAPI_CALL AllocatorReallocationStub(void*, void*, size_t, size_t, VkSystemAllocationScope) {
  AllocatorCallbackStubFatal("pfnReallocation");
}

static VKAPI_ATTR void VKAPI_CALL AllocatorFreeStub(void*, void*) {
  AllocatorCallbackStubFatal("pfnFree");
}

static VKAPI_ATTR void VKAPI_CALL AllocatorInternalAllocationStub(void*, size_t, VkInternalAllocationType, VkSystemAllocationScope) {
  AllocatorCallbackStubFatal("pfnInternalAllocation");
}

static VKAPI_ATTR void VKAPI_CALL AllocatorInternalFreeStub(void*, size_t, VkInternalAllocationType, VkSystemAllocationScope) {
  AllocatorCallbackStubFatal("pfnInternalFree");
}

void fex_custom_repack_entry(host_layout<VkAllocationCallbacks>& into, const guest_layout<VkAllocationCallbacks>&) {
  into.data.pfnAllocation = AllocatorAllocationStub;
  into.data.pfnReallocation = AllocatorReallocationStub;
  into.data.pfnFree = AllocatorFreeStub;
  into.data.pfnInternalAllocation = AllocatorInternalAllocationStub;
  into.data.pfnInternalFree = AllocatorInternalFreeStub;
}

bool fex_custom_repack_exit(guest_layout<VkAllocationCallbacks>&, const host_layout<VkAllocationCallbacks>&) {
  return false;
}
'''
host_path.write_text(host.replace(anchor, anchor + insert, 1))
