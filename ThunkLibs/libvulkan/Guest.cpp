/*
$info$
tags: thunklibs|Vulkan
$end_info$
*/

#define VK_USE_64_BIT_PTR_DEFINES 0

#define VK_USE_PLATFORM_XLIB_XRANDR_EXT
#define VK_USE_PLATFORM_XLIB_KHR
#define VK_USE_PLATFORM_XCB_KHR
#define VK_USE_PLATFORM_WAYLAND_KHR
#include <vulkan/vulkan.h>

#include "common/Guest.h"

#include <cstdio>
#include <dlfcn.h>
#include <functional>
#include <string_view>
#include <unordered_map>

#include "thunkgen_guest_libvulkan.inl"

extern "C" {

uintptr_t fex_vulkan_bridge_find_host_invoker(const char* name);
uintptr_t fex_vulkan_bridge_fatal_invoker();
uintptr_t fex_vulkan_bridge_xsync_unpacker();
uintptr_t fex_vulkan_bridge_xgetvisualinfo_unpacker();
uintptr_t fex_vulkan_bridge_xdisplaystring_unpacker();

// This variable controls the behavior of vkGetDevice/InstanceProcAddr for functions we don't know the signature of:
// - if false (default), we return a nullptr (since the application might have a fallback code path)
// - if true, we return a stub function that fatally errors upon being called
constexpr bool stub_unknown_functions = false;

// Unknown-function fatal invoker is owned by the resident bridge DSO.

static PFN_vkVoidFunction MakeGuestCallable(const char* origin, PFN_vkVoidFunction func, const char* name) {
  const auto GuestInvoker = fex_vulkan_bridge_find_host_invoker(name);
  if (!GuestInvoker) {
    fprintf(stderr, "%s: Unknown Vulkan function at address %p: %s\n", origin, func, name);
    if (stub_unknown_functions) {
      const auto StubHostPtrInvoker = fex_vulkan_bridge_fatal_invoker();
      LinkAddressToFunction((uintptr_t)func, StubHostPtrInvoker);
      return func;
    }
    return nullptr;
  }
  fprintf(stderr, "Linking address %p to resident host invoker %#zx\n", func, GuestInvoker);
  LinkAddressToFunction((uintptr_t)func, GuestInvoker);
  return func;
}

PFN_vkVoidFunction vkGetDeviceProcAddr(VkDevice a_0, const char* a_1) {
  auto Ret = fexfn_pack_vkGetDeviceProcAddr(a_0, a_1);
  if (!Ret) {
    return nullptr;
  }
  return MakeGuestCallable(__FUNCTION__, Ret, a_1);
}

PFN_vkVoidFunction vkGetInstanceProcAddr(VkInstance a_0, const char* a_1) {
  if (a_1 == std::string_view {"vkGetDeviceProcAddr"}) {
    return (PFN_vkVoidFunction)vkGetDeviceProcAddr;
  } else {
    auto Ret = fexfn_pack_vkGetInstanceProcAddr(a_0, a_1);
    if (!Ret) {
      return nullptr;
    }
    return MakeGuestCallable(__FUNCTION__, Ret, a_1);
  }
}
}

void OnInit() {
  // TODO: Load libX11 on-demand instead
  void* libx11 = dlopen("libX11.so.6", RTLD_LAZY);
  fexfn_pack_Vulkan_SetGuestXSync((uintptr_t)dlsym(libx11, "XSync"), fex_vulkan_bridge_xsync_unpacker());
  fexfn_pack_Vulkan_SetGuestXGetVisualInfo((uintptr_t)dlsym(libx11, "XGetVisualInfo"), fex_vulkan_bridge_xgetvisualinfo_unpacker());
  fexfn_pack_Vulkan_SetGuestXDisplayString((uintptr_t)dlsym(libx11, "XDisplayString"), fex_vulkan_bridge_xdisplaystring_unpacker());
}

LOAD_LIB_INIT(libvulkan, OnInit)
