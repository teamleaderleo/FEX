#define _GNU_SOURCE
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define RESOLVE(type, name) \
  type name = (type)dlsym(RTLD_DEFAULT, #name); \
  if (!(name)) { fprintf(stderr, "missing %s: %s\n", #name, dlerror()); return 3; }

static VkResult make_instance(PFN_vkCreateInstance create_instance, const char *name, VkInstance *out) {
  VkApplicationInfo app = {
    .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
    .pApplicationName = name,
    .apiVersion = VK_API_VERSION_1_0,
  };
  VkInstanceCreateInfo info = {
    .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
    .pApplicationInfo = &app,
  };
  return create_instance(&info, NULL, out);
}

int main(void) {
  void *vulkan = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_GLOBAL);
  if (!vulkan) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 2; }

  RESOLVE(PFN_vkCreateInstance, vkCreateInstance)
  RESOLVE(PFN_vkDestroyInstance, vkDestroyInstance)
  RESOLVE(PFN_vkGetInstanceProcAddr, vkGetInstanceProcAddr)
  RESOLVE(PFN_vkEnumeratePhysicalDevices, vkEnumeratePhysicalDevices)
  RESOLVE(PFN_vkGetPhysicalDeviceQueueFamilyProperties, vkGetPhysicalDeviceQueueFamilyProperties)
  RESOLVE(PFN_vkDestroyDevice, vkDestroyDevice)

  VkInstance a = VK_NULL_HANDLE, b = VK_NULL_HANDLE;
  VkResult result = make_instance(vkCreateInstance, "slot-A", &a);
  fprintf(stderr, "PROBE create-A result=%d instance=%p\n", result, (void *)a);
  if (result != VK_SUCCESS) return 10;
  result = make_instance(vkCreateInstance, "slot-B", &b);
  fprintf(stderr, "PROBE create-B result=%d instance=%p\n", result, (void *)b);
  if (result != VK_SUCCESS) return 11;

  PFN_vkCreateDevice create_a = (PFN_vkCreateDevice)vkGetInstanceProcAddr(a, "vkCreateDevice");
  PFN_vkCreateDevice create_b = (PFN_vkCreateDevice)vkGetInstanceProcAddr(b, "vkCreateDevice");
  fprintf(stderr, "PROBE create-device-ptrs A=%p B=%p\n", (void *)create_a, (void *)create_b);
  if (!create_a || !create_b) return 12;

  uint32_t physical_count = 0;
  result = vkEnumeratePhysicalDevices(b, &physical_count, NULL);
  if (result != VK_SUCCESS || physical_count == 0) return 13;
  VkPhysicalDevice *physical = calloc(physical_count, sizeof(*physical));
  if (!physical) return 14;
  result = vkEnumeratePhysicalDevices(b, &physical_count, physical);
  if (result != VK_SUCCESS) return 15;
  VkPhysicalDevice p = physical[0];
  free(physical);

  uint32_t queue_count = 0;
  vkGetPhysicalDeviceQueueFamilyProperties(p, &queue_count, NULL);
  if (queue_count == 0) return 16;
  VkQueueFamilyProperties *queues = calloc(queue_count, sizeof(*queues));
  if (!queues) return 17;
  vkGetPhysicalDeviceQueueFamilyProperties(p, &queue_count, queues);
  uint32_t queue_index = 0;
  while (queue_index < queue_count && queues[queue_index].queueCount == 0) ++queue_index;
  free(queues);
  if (queue_index == queue_count) return 18;

  float priority = 1.0f;
  VkDeviceQueueCreateInfo queue_info = {
    .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
    .queueFamilyIndex = queue_index,
    .queueCount = 1,
    .pQueuePriorities = &priority,
  };
  VkDeviceCreateInfo device_info = {
    .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
    .queueCreateInfoCount = 1,
    .pQueueCreateInfos = &queue_info,
  };

  VkDevice device = VK_NULL_HANDLE;
  fprintf(stderr, "PROBE invoke-B physical=%p\n", (void *)p);
  fflush(stderr);
  result = create_b(p, &device_info, NULL, &device);
  fprintf(stderr, "PROBE invoke-B-return result=%d device=%p\n", result, (void *)device);
  fflush(stderr);
  if (result == VK_SUCCESS && device) vkDestroyDevice(device, NULL);

  vkDestroyInstance(b, NULL);
  vkDestroyInstance(a, NULL);
  return result == VK_SUCCESS ? 0 : 20;
}
