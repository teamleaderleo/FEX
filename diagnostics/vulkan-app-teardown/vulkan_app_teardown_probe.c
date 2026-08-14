#define _GNU_SOURCE
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>

#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int CountVulkanMappings(void) {
  FILE *maps = fopen("/proc/self/maps", "r");
  if (!maps) {
    return -1;
  }
  char line[4096];
  int count = 0;
  while (fgets(line, sizeof(line), maps)) {
    if (strstr(line, "libvulkan")) {
      ++count;
    }
  }
  fclose(maps);
  return count;
}

static void Die(const char *what) {
  fprintf(stderr, "APP fail %s dlerror=%s\n", what, dlerror());
  exit(2);
}

int main(int argc, char **argv) {
  setvbuf(stdout, NULL, _IONBF, 0);
  setvbuf(stderr, NULL, _IONBF, 0);

  const int pin = argc == 2 && strcmp(argv[1], "pin") == 0;
  if (argc > 2 || (argc == 2 && !pin)) {
    fprintf(stderr, "usage: %s [pin]\n", argv[0]);
    return 64;
  }

  fprintf(stderr, "APP begin maps=%d pin=%d\n", CountVulkanMappings(), pin);

  void *lib = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!lib) Die("dlopen libvulkan");

  void *pin_handle = NULL;
  if (pin) {
    pin_handle = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!pin_handle) Die("pin dlopen libvulkan");
  }

  PFN_vkGetInstanceProcAddr gipa = (PFN_vkGetInstanceProcAddr)dlsym(lib, "vkGetInstanceProcAddr");
  if (!gipa) Die("dlsym vkGetInstanceProcAddr");

  PFN_vkCreateInstance create_instance =
    (PFN_vkCreateInstance)gipa(VK_NULL_HANDLE, "vkCreateInstance");
  if (!create_instance) {
    fprintf(stderr, "APP fail vkCreateInstance PFN null\n");
    return 3;
  }

  VkApplicationInfo app = {
    .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
    .pApplicationName = "fex-vulkan-app-teardown",
    .applicationVersion = VK_MAKE_API_VERSION(0, 1, 0, 0),
    .pEngineName = "none",
    .engineVersion = 0,
    .apiVersion = VK_API_VERSION_1_0,
  };
  VkInstanceCreateInfo ci = {
    .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
    .pApplicationInfo = &app,
  };

  VkInstance instance = VK_NULL_HANDLE;
  VkResult result = create_instance(&ci, NULL, &instance);
  fprintf(stderr, "APP create result=%d instance=%p maps=%d\n", result, (void *)instance, CountVulkanMappings());
  if (result != VK_SUCCESS || instance == VK_NULL_HANDLE) {
    return 4;
  }

  PFN_vkEnumeratePhysicalDevices enumerate =
    (PFN_vkEnumeratePhysicalDevices)gipa(instance, "vkEnumeratePhysicalDevices");
  PFN_vkGetPhysicalDeviceProperties get_properties =
    (PFN_vkGetPhysicalDeviceProperties)gipa(instance, "vkGetPhysicalDeviceProperties");
  PFN_vkDestroyInstance destroy_instance =
    (PFN_vkDestroyInstance)gipa(instance, "vkDestroyInstance");
  if (!enumerate || !get_properties || !destroy_instance) {
    fprintf(stderr, "APP fail dynamic PFN enumerate=%p props=%p destroy=%p\n",
            (void *)enumerate, (void *)get_properties, (void *)destroy_instance);
    return 5;
  }

  fprintf(stderr,
          "APP pfn gipa=%p create=%p enumerate=%p props=%p destroy=%p\n",
          (void *)gipa, (void *)create_instance, (void *)enumerate,
          (void *)get_properties, (void *)destroy_instance);

  uint32_t count = 0;
  result = enumerate(instance, &count, NULL);
  fprintf(stderr, "APP enumerate-count result=%d count=%u\n", result, count);
  if (result != VK_SUCCESS && result != VK_INCOMPLETE) {
    return 6;
  }

  VkPhysicalDevice *devices = NULL;
  if (count) {
    devices = calloc(count, sizeof(*devices));
    if (!devices) return 7;
    uint32_t requested = count;
    result = enumerate(instance, &requested, devices);
    fprintf(stderr, "APP enumerate-list result=%d count=%u\n", result, requested);
    if (result != VK_SUCCESS && result != VK_INCOMPLETE) {
      return 8;
    }
    count = requested;
  }

  for (uint32_t i = 0; i < count; ++i) {
    VkPhysicalDeviceProperties props;
    memset(&props, 0, sizeof(props));
    get_properties(devices[i], &props);
    fprintf(stderr, "APP device[%u] api=0x%x vendor=0x%x device=0x%x name=%s\n",
            i, props.apiVersion, props.vendorID, props.deviceID, props.deviceName);
  }
  free(devices);

  fprintf(stderr, "APP destroy-instance begin\n");
  destroy_instance(instance, NULL);
  fprintf(stderr, "APP destroy-instance done maps=%d\n", CountVulkanMappings());

  if (dlclose(lib) != 0) Die("dlclose libvulkan");
  fprintf(stderr, "APP dlclose done maps=%d\n", CountVulkanMappings());

  if (pin_handle) {
    fprintf(stderr, "APP pin intentionally retained through normal return maps=%d handle=%p\n",
            CountVulkanMappings(), pin_handle);
  }

  fprintf(stderr, "APP normal-return\n");
  return 0;
}
