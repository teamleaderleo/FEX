#define VK_USE_64_BIT_PTR_DEFINES 0

#define VK_USE_PLATFORM_XLIB_KHR
#include <vulkan/vulkan.h>

#include <X11/Xlib.h>
#include <X11/Xutil.h>

#include "common/Guest.h"
#include "thunkgen_guest_libvulkan_bridge.inl"

#define FEX_VULKAN_BRIDGE_EXPORT __attribute__((visibility("default")))

// These forwarding targets live beside their unpackers. The companion's X11
// dependency also keeps the implementation they call alive after libvulkan closes.
extern "C" FEX_VULKAN_BRIDGE_EXPORT int FEXVulkanBridgeXSync(Display* display, Bool discard) {
  return XSync(display, discard);
}

extern "C" FEX_VULKAN_BRIDGE_EXPORT XVisualInfo*
FEXVulkanBridgeXGetVisualInfo(Display* display, long mask, XVisualInfo* info, int* count) {
  return XGetVisualInfo(display, mask, info, count);
}

extern "C" FEX_VULKAN_BRIDGE_EXPORT char* FEXVulkanBridgeXDisplayString(Display* display) {
  return XDisplayString(display);
}

extern "C" FEX_VULKAN_BRIDGE_EXPORT uintptr_t FEXVulkanBridgeXSyncUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(FEXVulkanBridgeXSync)>::Unpack);
}

extern "C" FEX_VULKAN_BRIDGE_EXPORT uintptr_t FEXVulkanBridgeXGetVisualInfoUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(FEXVulkanBridgeXGetVisualInfo)>::Unpack);
}

extern "C" FEX_VULKAN_BRIDGE_EXPORT uintptr_t FEXVulkanBridgeXDisplayStringUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(FEXVulkanBridgeXDisplayString)>::Unpack);
}
