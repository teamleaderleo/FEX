#define VK_USE_64_BIT_PTR_DEFINES 0
#define VK_USE_PLATFORM_XLIB_XRANDR_EXT
#define VK_USE_PLATFORM_XLIB_KHR
#define VK_USE_PLATFORM_XCB_KHR
#define VK_USE_PLATFORM_WAYLAND_KHR
#include <vulkan/vulkan.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>

#include "common/Guest.h"
#include "thunkgen_guest_libvulkan_bridge.inl"

extern "C" uintptr_t FEXVulkanBridgeXSyncUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(XSync)>::Unpack);
}

extern "C" uintptr_t FEXVulkanBridgeXGetVisualInfoUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(XGetVisualInfo)>::Unpack);
}

extern "C" uintptr_t FEXVulkanBridgeXDisplayStringUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(XDisplayString)>::Unpack);
}
