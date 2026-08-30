#define VK_USE_64_BIT_PTR_DEFINES 0
#define VK_USE_PLATFORM_XLIB_KHR
#include <vulkan/vulkan.h>

#include <stdio.h>
#include <string.h>

VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateInstanceVersion(uint32_t* version) {
  if (version) *version = VK_MAKE_API_VERSION(0, 1, 3, 151);
  return VK_SUCCESS;
}

VKAPI_ATTR VkBool32 VKAPI_CALL vkGetPhysicalDeviceXlibPresentationSupportKHR(
  VkPhysicalDevice physical_device, uint32_t queue_family, Display* display, VisualID visual_id) {
  fprintf(stderr, "HOST_VULKAN_XLIB physical=%p queue=%u display=%p visual=%lu\n", (void*)physical_device, queue_family,
          (void*)display, (unsigned long)visual_id);
  return display ? VK_TRUE : VK_FALSE;
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateDevice(
  VkPhysicalDevice physical_device, const VkDeviceCreateInfo* create_info, const VkAllocationCallbacks* allocator, VkDevice* device) {
  (void)physical_device;
  (void)create_info;
  (void)allocator;
  (void)device;
  return VK_ERROR_INITIALIZATION_FAILED;
}

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetDeviceProcAddr(VkDevice device, const char* name) {
  (void)device;
  if (strcmp(name, "vkGetDeviceProcAddr") == 0) return (PFN_vkVoidFunction)vkGetDeviceProcAddr;
  return NULL;
}

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr(VkInstance instance, const char* name) {
  (void)instance;
  if (strcmp(name, "vkGetInstanceProcAddr") == 0) return (PFN_vkVoidFunction)vkGetInstanceProcAddr;
  if (strcmp(name, "vkGetDeviceProcAddr") == 0) return (PFN_vkVoidFunction)vkGetDeviceProcAddr;
  if (strcmp(name, "vkEnumerateInstanceVersion") == 0) return (PFN_vkVoidFunction)vkEnumerateInstanceVersion;
  if (strcmp(name, "vkCreateDevice") == 0) return (PFN_vkVoidFunction)vkCreateDevice;
  if (strcmp(name, "vkGetPhysicalDeviceXlibPresentationSupportKHR") == 0) {
    return (PFN_vkVoidFunction)vkGetPhysicalDeviceXlibPresentationSupportKHR;
  }
  return NULL;
}
