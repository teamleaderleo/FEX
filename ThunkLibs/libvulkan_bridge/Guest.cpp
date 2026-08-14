#define VK_USE_64_BIT_PTR_DEFINES 0
#define VK_USE_PLATFORM_XLIB_KHR
#include <vulkan/vulkan.h>
#include <X11/Xlib.h>

#include "common/Guest.h"
#include "thunkgen_guest_libvulkan_bridge.inl"

extern "C" uintptr_t FEXVulkanBridgeEnumerateInstanceVersionInvoker() {
  using Signature = VkResult(uint32_t*);
  return reinterpret_cast<uintptr_t>(
    &CallHostFunction<fexthunks_invoke_callback<Signature>, VkResult, uint32_t*>);
}

extern "C" uintptr_t FEXVulkanBridgeXSyncUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(XSync)>::Unpack);
}

extern "C" uintptr_t FEXVulkanBridgeXGetVisualInfoUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(XGetVisualInfo)>::Unpack);
}

extern "C" uintptr_t FEXVulkanBridgeXDisplayStringUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(XDisplayString)>::Unpack);
}
