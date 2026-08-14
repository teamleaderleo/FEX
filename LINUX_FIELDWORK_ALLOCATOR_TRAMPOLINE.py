from pathlib import Path

iface_path = Path("ThunkLibs/libvulkan/libvulkan_interface.cpp")
iface = iface_path.read_text()
setup_anchor = '''void Vulkan_SetGuestXDisplayString(uintptr_t, uintptr_t);
template<>
struct fex_gen_config<Vulkan_SetGuestXSync> : fexgen::custom_guest_entrypoint, fexgen::custom_host_impl {};'''
setup_repl = '''void Vulkan_SetGuestXDisplayString(uintptr_t, uintptr_t);
void Vulkan_SetGuestAllocatorUnpackers(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
template<>
struct fex_gen_config<Vulkan_SetGuestXSync> : fexgen::custom_guest_entrypoint, fexgen::custom_host_impl {};'''
if iface.count(setup_anchor) != 1:
    raise SystemExit(f"expected one allocator setup declaration anchor, found {iface.count(setup_anchor)}")
iface = iface.replace(setup_anchor, setup_repl, 1)
config_anchor = '''template<>
struct fex_gen_config<Vulkan_SetGuestXDisplayString> : fexgen::custom_guest_entrypoint, fexgen::custom_host_impl {};
'''
config_repl = config_anchor + '''template<>
struct fex_gen_config<Vulkan_SetGuestAllocatorUnpackers> : fexgen::custom_guest_entrypoint, fexgen::custom_host_impl {};
'''
if iface.count(config_anchor) != 1:
    raise SystemExit(f"expected one allocator setup config anchor, found {iface.count(config_anchor)}")
iface = iface.replace(config_anchor, config_repl, 1)

allocator_old = '''// TODO: Should not be opaque, but it's usually NULL anyway. Supporting the contained function pointers will need more work.
template<>
struct fex_gen_type<VkAllocationCallbacks> : fexgen::opaque_type {};'''
allocator_new = '''// Linux Fieldwork experiment: make VkAllocationCallbacks repackable and
// mediate all callback-bearing members with host-to-guest trampolines.
template<>
struct fex_gen_type<VkAllocationCallbacks> {};
template<>
struct fex_gen_config<&VkAllocationCallbacks::pUserData> : fexgen::custom_repack {};
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
if iface.count(allocator_old) != 1:
    raise SystemExit(f"expected one VkAllocationCallbacks opaque annotation, found {iface.count(allocator_old)}")
iface_path.write_text(iface.replace(allocator_old, allocator_new, 1))

host_path = Path("ThunkLibs/libvulkan/Host.cpp")
host = host_path.read_text()
host_setup_anchor = '''static void fexfn_impl_libvulkan_Vulkan_SetGuestXDisplayString(uintptr_t GuestTarget, uintptr_t GuestUnpacker) {
  MakeHostTrampolineForGuestFunctionAt(GuestTarget, GuestUnpacker, &x11_manager.GuestXDisplayString);
}
'''
host_setup_repl = host_setup_anchor + r'''

struct AllocatorGuestUnpackers {
  uintptr_t Allocation {};
  uintptr_t Reallocation {};
  uintptr_t Free {};
  uintptr_t InternalAllocation {};
  uintptr_t InternalFree {};
};
static AllocatorGuestUnpackers allocator_guest_unpackers;

static void fexfn_impl_libvulkan_Vulkan_SetGuestAllocatorUnpackers(uintptr_t Allocation, uintptr_t Reallocation, uintptr_t Free,
                                                                   uintptr_t InternalAllocation, uintptr_t InternalFree) {
  allocator_guest_unpackers = {Allocation, Reallocation, Free, InternalAllocation, InternalFree};
}

void fex_custom_repack_entry(host_layout<VkAllocationCallbacks>& into, const guest_layout<VkAllocationCallbacks>& from) {
  into.data.pUserData = const_cast<void*>(from.data.pUserData.force_get_host_pointer());

  if (from.data.pfnAllocation.data) {
    MakeHostTrampolineForGuestFunctionAt(from.data.pfnAllocation.data, allocator_guest_unpackers.Allocation, &into.data.pfnAllocation);
  } else {
    into.data.pfnAllocation = nullptr;
  }
  if (from.data.pfnReallocation.data) {
    MakeHostTrampolineForGuestFunctionAt(from.data.pfnReallocation.data, allocator_guest_unpackers.Reallocation, &into.data.pfnReallocation);
  } else {
    into.data.pfnReallocation = nullptr;
  }
  if (from.data.pfnFree.data) {
    MakeHostTrampolineForGuestFunctionAt(from.data.pfnFree.data, allocator_guest_unpackers.Free, &into.data.pfnFree);
  } else {
    into.data.pfnFree = nullptr;
  }
  if (from.data.pfnInternalAllocation.data) {
    MakeHostTrampolineForGuestFunctionAt(from.data.pfnInternalAllocation.data, allocator_guest_unpackers.InternalAllocation,
                                         &into.data.pfnInternalAllocation);
  } else {
    into.data.pfnInternalAllocation = nullptr;
  }
  if (from.data.pfnInternalFree.data) {
    MakeHostTrampolineForGuestFunctionAt(from.data.pfnInternalFree.data, allocator_guest_unpackers.InternalFree, &into.data.pfnInternalFree);
  } else {
    into.data.pfnInternalFree = nullptr;
  }
}

bool fex_custom_repack_exit(guest_layout<VkAllocationCallbacks>&, const host_layout<VkAllocationCallbacks>&) {
  return false;
}
'''
if host.count(host_setup_anchor) != 1:
    raise SystemExit(f"expected one host allocator setup anchor, found {host.count(host_setup_anchor)}")
host_path.write_text(host.replace(host_setup_anchor, host_setup_repl, 1))

guest_path = Path("ThunkLibs/libvulkan/Guest.cpp")
guest = guest_path.read_text()
guest_anchor = '''  fexfn_pack_Vulkan_SetGuestXDisplayString((uintptr_t)dlsym(libx11, "XDisplayString"), (uintptr_t)CallbackUnpack<decltype(XDisplayString)>::Unpack);
}'''
guest_repl = '''  fexfn_pack_Vulkan_SetGuestXDisplayString((uintptr_t)dlsym(libx11, "XDisplayString"), (uintptr_t)CallbackUnpack<decltype(XDisplayString)>::Unpack);
  fexfn_pack_Vulkan_SetGuestAllocatorUnpackers(
    (uintptr_t)CallbackUnpack<PFN_vkAllocationFunction>::Unpack,
    (uintptr_t)CallbackUnpack<PFN_vkReallocationFunction>::Unpack,
    (uintptr_t)CallbackUnpack<PFN_vkFreeFunction>::Unpack,
    (uintptr_t)CallbackUnpack<PFN_vkInternalAllocationNotification>::Unpack,
    (uintptr_t)CallbackUnpack<PFN_vkInternalFreeNotification>::Unpack);
}'''
if guest.count(guest_anchor) != 1:
    raise SystemExit(f"expected one guest allocator setup anchor, found {guest.count(guest_anchor)}")
guest_path.write_text(guest.replace(guest_anchor, guest_repl, 1))
