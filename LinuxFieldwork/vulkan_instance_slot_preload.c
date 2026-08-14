#define _GNU_SOURCE
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

static VkInstance instance_a;
static VkInstance instance_b;

static PFN_vkCreateInstance next_create_instance(void) {
  static PFN_vkCreateInstance fn;
  if (!fn) fn = (PFN_vkCreateInstance)dlsym(RTLD_NEXT, "vkCreateInstance");
  return fn;
}

static PFN_vkGetInstanceProcAddr next_gipa(void) {
  static PFN_vkGetInstanceProcAddr fn;
  if (!fn) fn = (PFN_vkGetInstanceProcAddr)dlsym(RTLD_NEXT, "vkGetInstanceProcAddr");
  return fn;
}

static PFN_vkCreateDevice next_create_device(void) {
  static PFN_vkCreateDevice fn;
  if (!fn) fn = (PFN_vkCreateDevice)dlsym(RTLD_NEXT, "vkCreateDevice");
  return fn;
}

static VKAPI_ATTR VkResult VKAPI_CALL slot_create_device_a(
    VkPhysicalDevice physicalDevice, const VkDeviceCreateInfo *pCreateInfo,
    const VkAllocationCallbacks *pAllocator, VkDevice *pDevice) {
  fprintf(stderr, "SLOT_CREATE_WRAPPER=A physical=%p\n", (void *)physicalDevice);
  fflush(stderr);
  PFN_vkCreateDevice fn = next_create_device();
  return fn ? fn(physicalDevice, pCreateInfo, pAllocator, pDevice) : VK_ERROR_INITIALIZATION_FAILED;
}

static VKAPI_ATTR VkResult VKAPI_CALL slot_create_device_b(
    VkPhysicalDevice physicalDevice, const VkDeviceCreateInfo *pCreateInfo,
    const VkAllocationCallbacks *pAllocator, VkDevice *pDevice) {
  fprintf(stderr, "SLOT_CREATE_WRAPPER=B physical=%p\n", (void *)physicalDevice);
  fflush(stderr);
  PFN_vkCreateDevice fn = next_create_device();
  return fn ? fn(physicalDevice, pCreateInfo, pAllocator, pDevice) : VK_ERROR_INITIALIZATION_FAILED;
}

VKAPI_ATTR VkResult VKAPI_CALL vkCreateInstance(
    const VkInstanceCreateInfo *pCreateInfo, const VkAllocationCallbacks *pAllocator,
    VkInstance *pInstance) {
  PFN_vkCreateInstance fn = next_create_instance();
  if (!fn) return VK_ERROR_INITIALIZATION_FAILED;
  VkResult result = fn(pCreateInfo, pAllocator, pInstance);
  const char *name = pCreateInfo && pCreateInfo->pApplicationInfo
                         ? pCreateInfo->pApplicationInfo->pApplicationName
                         : NULL;
  if (result == VK_SUCCESS && name && pInstance) {
    if (!strcmp(name, "slot-A")) instance_a = *pInstance;
    if (!strcmp(name, "slot-B")) instance_b = *pInstance;
  }
  fprintf(stderr, "SLOT_CREATE_INSTANCE name=%s result=%d instance=%p A=%p B=%p\n",
          name ? name : "(null)", result, pInstance ? (void *)*pInstance : NULL,
          (void *)instance_a, (void *)instance_b);
  fflush(stderr);
  return result;
}

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr(VkInstance instance, const char *pName) {
  if (pName && !strcmp(pName, "vkCreateDevice")) {
    if (instance && instance == instance_a) {
      fprintf(stderr, "SLOT_GIPA instance=A wrapper=A\n");
      fflush(stderr);
      return (PFN_vkVoidFunction)slot_create_device_a;
    }
    if (instance && instance == instance_b) {
      fprintf(stderr, "SLOT_GIPA instance=B wrapper=B\n");
      fflush(stderr);
      return (PFN_vkVoidFunction)slot_create_device_b;
    }
  }
  PFN_vkGetInstanceProcAddr fn = next_gipa();
  return fn ? fn(instance, pName) : NULL;
}
