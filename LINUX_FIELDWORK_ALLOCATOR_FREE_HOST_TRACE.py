from pathlib import Path

path = Path("ThunkLibs/libvulkan/Host.cpp")
text = path.read_text()

anchor = '''static AllocatorGuestUnpackers allocator_guest_unpackers;

static void fexfn_impl_libvulkan_Vulkan_SetGuestAllocatorUnpackers'''
replacement = r'''static AllocatorGuestUnpackers allocator_guest_unpackers;
static PFN_vkFreeFunction allocator_free_guest_trampoline;

static VKAPI_ATTR void VKAPI_CALL AllocatorFreeHostTrace(void* user, void* memory) {
  fprintf(stderr, "HOST_FREE_WRAPPER_ENTER user=%p memory=%p trampoline=%p\n", user, memory, (void*)allocator_free_guest_trampoline);
  fflush(stderr);
  allocator_free_guest_trampoline(user, memory);
  fprintf(stderr, "HOST_FREE_WRAPPER_RETURN memory=%p\n", memory);
  fflush(stderr);
}

static void fexfn_impl_libvulkan_Vulkan_SetGuestAllocatorUnpackers'''
if text.count(anchor) != 1:
    raise SystemExit(f"expected one free host trace anchor, found {text.count(anchor)}")
text = text.replace(anchor, replacement, 1)

old = '''  if (from.data.pfnFree.data) {
    MakeHostTrampolineForGuestFunctionAt(from.data.pfnFree.data, allocator_guest_unpackers.Free, &into.data.pfnFree);
  } else {
    into.data.pfnFree = nullptr;
  }'''
new = '''  if (from.data.pfnFree.data) {
    MakeHostTrampolineForGuestFunctionAt(from.data.pfnFree.data, allocator_guest_unpackers.Free, &allocator_free_guest_trampoline);
    into.data.pfnFree = AllocatorFreeHostTrace;
  } else {
    allocator_free_guest_trampoline = nullptr;
    into.data.pfnFree = nullptr;
  }'''
if text.count(old) != 1:
    raise SystemExit(f"expected one pfnFree mediation block, found {text.count(old)}")
path.write_text(text.replace(old, new, 1))
