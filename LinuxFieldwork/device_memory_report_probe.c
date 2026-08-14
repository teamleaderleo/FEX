#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static volatile unsigned callback_count;

__attribute__((used,noinline)) static void VKAPI_PTR report_body(const VkDeviceMemoryReportCallbackDataEXT *data, void *user) {
  (void)user;
  ++callback_count;
  fprintf(stderr, "MEM_REPORT callback=%u type=%u size=%llu objectType=%u objectId=%llu\n",
          callback_count,
          data ? (unsigned)data->type : 0u,
          data ? (unsigned long long)data->size : 0ull,
          data ? (unsigned)data->objectType : 0u,
          data ? (unsigned long long)data->objectId : 0ull);
  fflush(stderr);
}

#if defined(__x86_64__)
__attribute__((naked,noinline)) static void VKAPI_PTR report_cb(const VkDeviceMemoryReportCallbackDataEXT *data, void *user) {
  (void)data; (void)user;
  __asm__ volatile(".byte 0xe9,0,0,0,0\n\tjmp report_body");
}
#else
static void VKAPI_PTR report_cb(const VkDeviceMemoryReportCallbackDataEXT *data, void *user) {
  report_body(data, user);
}
#endif

#define RESOLVE(type, name) \
  type name = (type)dlsym(vulkan, #name); \
  if (!(name)) { fprintf(stderr, "SKIP missing %s\n", #name); return 77; }

int main(int argc, char **argv) {
  int support_only = argc == 2 && !strcmp(argv[1], "--support");
  if (argc > 2 || (argc == 2 && !support_only)) return 64;
  void *vulkan = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!vulkan) { fprintf(stderr, "SKIP dlopen %s\n", dlerror()); return 77; }

  RESOLVE(PFN_vkCreateInstance, vkCreateInstance)
  RESOLVE(PFN_vkDestroyInstance, vkDestroyInstance)
  RESOLVE(PFN_vkEnumeratePhysicalDevices, vkEnumeratePhysicalDevices)
  RESOLVE(PFN_vkEnumerateDeviceExtensionProperties, vkEnumerateDeviceExtensionProperties)
  RESOLVE(PFN_vkGetPhysicalDeviceQueueFamilyProperties, vkGetPhysicalDeviceQueueFamilyProperties)
  RESOLVE(PFN_vkGetPhysicalDeviceMemoryProperties, vkGetPhysicalDeviceMemoryProperties)
  RESOLVE(PFN_vkCreateDevice, vkCreateDevice)
  RESOLVE(PFN_vkDestroyDevice, vkDestroyDevice)
  RESOLVE(PFN_vkAllocateMemory, vkAllocateMemory)
  RESOLVE(PFN_vkFreeMemory, vkFreeMemory)

  VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "fex-mem-report", .apiVersion = VK_API_VERSION_1_1};
  VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app};
  VkInstance instance = VK_NULL_HANDLE;
  VkResult result = vkCreateInstance(&ici, NULL, &instance);
  if (result != VK_SUCCESS) { fprintf(stderr, "SKIP create-instance=%d\n", result); return 77; }

  uint32_t physical_count = 0;
  result = vkEnumeratePhysicalDevices(instance, &physical_count, NULL);
  if (result != VK_SUCCESS || physical_count == 0) { vkDestroyInstance(instance, NULL); return 77; }
  VkPhysicalDevice *physical_devices = calloc(physical_count, sizeof(*physical_devices));
  if (!physical_devices) return 70;
  result = vkEnumeratePhysicalDevices(instance, &physical_count, physical_devices);
  if (result != VK_SUCCESS) return 71;
  VkPhysicalDevice physical = physical_devices[0];
  free(physical_devices);

  uint32_t ext_count = 0;
  result = vkEnumerateDeviceExtensionProperties(physical, NULL, &ext_count, NULL);
  if (result != VK_SUCCESS) return 72;
  VkExtensionProperties *props = calloc(ext_count ? ext_count : 1, sizeof(*props));
  if (!props) return 73;
  result = vkEnumerateDeviceExtensionProperties(physical, NULL, &ext_count, props);
  if (result != VK_SUCCESS) return 74;
  int supported = 0;
  for (uint32_t i = 0; i < ext_count; ++i) {
    if (!strcmp(props[i].extensionName, VK_EXT_DEVICE_MEMORY_REPORT_EXTENSION_NAME)) { supported = 1; break; }
  }
  free(props);
  fprintf(stderr, "MEM_REPORT_SUPPORT supported=%d physical=%p\n", supported, (void *)physical);
  fflush(stderr);
  if (!supported) { vkDestroyInstance(instance, NULL); return 77; }
  if (support_only) { vkDestroyInstance(instance, NULL); return 0; }

  uint32_t queue_count = 0;
  vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, NULL);
  VkQueueFamilyProperties *queues = calloc(queue_count ? queue_count : 1, sizeof(*queues));
  if (!queues) return 75;
  vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, queues);
  uint32_t qi = 0;
  while (qi < queue_count && queues[qi].queueCount == 0) ++qi;
  free(queues);
  if (qi == queue_count) return 76;

  VkPhysicalDeviceMemoryProperties memory_properties;
  vkGetPhysicalDeviceMemoryProperties(physical, &memory_properties);
  if (memory_properties.memoryTypeCount == 0) return 78;

  float priority = 1.0f;
  VkDeviceQueueCreateInfo qci = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = qi, .queueCount = 1, .pQueuePriorities = &priority};
  VkDeviceDeviceMemoryReportCreateInfoEXT report = {
    .sType = VK_STRUCTURE_TYPE_DEVICE_DEVICE_MEMORY_REPORT_CREATE_INFO_EXT,
    .pNext = NULL,
    .flags = 0,
    .pfnUserCallback = report_cb,
    .pUserData = NULL,
  };
  const char *extensions[] = {VK_EXT_DEVICE_MEMORY_REPORT_EXTENSION_NAME};
  VkDeviceCreateInfo dci = {
    .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
    .pNext = &report,
    .queueCreateInfoCount = 1,
    .pQueueCreateInfos = &qci,
    .enabledExtensionCount = 1,
    .ppEnabledExtensionNames = extensions,
  };

  fprintf(stderr, "MEM_REPORT_CALLBACK=%p\nMARK create-device-enter\n", (void *)report_cb);
  fflush(stderr);
  VkDevice device = VK_NULL_HANDLE;
  result = vkCreateDevice(physical, &dci, NULL, &device);
  fprintf(stderr, "MARK create-device-return result=%d callbacks=%u device=%p\n", result, callback_count, (void *)device);
  fflush(stderr);
  if (result != VK_SUCCESS) { vkDestroyInstance(instance, NULL); return 79; }

  VkMemoryAllocateInfo mai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = 4096, .memoryTypeIndex = 0};
  VkDeviceMemory memory = VK_NULL_HANDLE;
  fprintf(stderr, "MARK allocate-enter callbacks=%u\n", callback_count);
  fflush(stderr);
  result = vkAllocateMemory(device, &mai, NULL, &memory);
  fprintf(stderr, "MARK allocate-return result=%d callbacks=%u memory=%llu\n", result, callback_count, (unsigned long long)memory);
  fflush(stderr);
  if (result == VK_SUCCESS && memory != VK_NULL_HANDLE) vkFreeMemory(device, memory, NULL);
  fprintf(stderr, "MARK destroy-device-enter callbacks=%u\n", callback_count);
  fflush(stderr);
  vkDestroyDevice(device, NULL);
  fprintf(stderr, "MARK destroy-device-return callbacks=%u\n", callback_count);
  fflush(stderr);
  vkDestroyInstance(instance, NULL);
  return callback_count ? 0 : 20;
}
