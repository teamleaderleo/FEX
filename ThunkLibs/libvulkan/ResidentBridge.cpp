#define VK_USE_64_BIT_PTR_DEFINES 0
#define VK_USE_PLATFORM_XLIB_XRANDR_EXT
#define VK_USE_PLATFORM_XLIB_KHR
#define VK_USE_PLATFORM_XCB_KHR
#define VK_USE_PLATFORM_WAYLAND_KHR
#include <vulkan/vulkan.h>

#include "common/Guest.h"
#include "thunkgen_guest_libvulkan.inl"

extern "C" uintptr_t fex_vulkan_bridge_enumerate_invoker() {
  return reinterpret_cast<uintptr_t>(GetCallerForHostFunction(vkEnumerateInstanceVersion));
}

extern "C" uintptr_t fex_vulkan_bridge_xsync_unpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(XSync)>::Unpack);
}

extern "C" uintptr_t fex_vulkan_bridge_xgetvisualinfo_unpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(XGetVisualInfo)>::Unpack);
}

extern "C" uintptr_t fex_vulkan_bridge_xdisplaystring_unpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(XDisplayString)>::Unpack);
}
