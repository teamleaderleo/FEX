#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

static volatile unsigned callback_count;

__attribute__((used,noinline)) static PFN_vkVoidFunction VKAPI_PTR direct_gipa_body(VkInstance instance, const char *name) {
  (void)instance;
  ++callback_count;
  fprintf(stderr, "CALLBACK direct-driver count=%u name=%s\n", callback_count, name ? name : "(null)");
  fflush(stderr);
  return NULL;
}

#if defined(__x86_64__)
__attribute__((naked,noinline)) static PFN_vkVoidFunction VKAPI_PTR direct_gipa(VkInstance instance, const char *name) {
  (void)instance;
  (void)name;
  __asm__ volatile(".byte 0xe9,0,0,0,0\n\tjmp direct_gipa_body");
}
#else
static PFN_vkVoidFunction VKAPI_PTR direct_gipa(VkInstance instance, const char *name) {
  return direct_gipa_body(instance, name);
}
#endif

int main(void) {
  void *vulkan = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!vulkan) {
    fprintf(stderr, "SKIP dlopen: %s\n", dlerror());
    return 77;
  }

  PFN_vkCreateInstance create_instance = (PFN_vkCreateInstance)dlsym(vulkan, "vkCreateInstance");
  if (!create_instance) {
    fprintf(stderr, "SKIP vkCreateInstance\n");
    return 77;
  }

  const char *extensions[] = {VK_LUNARG_DIRECT_DRIVER_LOADING_EXTENSION_NAME};
  VkDirectDriverLoadingInfoLUNARG driver = {
    .sType = VK_STRUCTURE_TYPE_DIRECT_DRIVER_LOADING_INFO_LUNARG,
    .pNext = NULL,
    .flags = 0,
    .pfnGetInstanceProcAddr = direct_gipa,
  };
  VkDirectDriverLoadingListLUNARG list = {
    .sType = VK_STRUCTURE_TYPE_DIRECT_DRIVER_LOADING_LIST_LUNARG,
    .pNext = NULL,
    .mode = VK_DIRECT_DRIVER_LOADING_MODE_EXCLUSIVE_LUNARG,
    .driverCount = 1,
    .pDrivers = &driver,
  };
  VkApplicationInfo app = {
    .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
    .pApplicationName = "fex-direct-driver-probe",
    .apiVersion = VK_API_VERSION_1_0,
  };
  VkInstanceCreateInfo create_info = {
    .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
    .pNext = &list,
    .pApplicationInfo = &app,
    .enabledExtensionCount = 1,
    .ppEnabledExtensionNames = extensions,
  };

  fprintf(stderr, "GUEST_CALLBACK=%p\n", (void *)direct_gipa);
  fprintf(stderr, "MARK create-enter\n");
  fflush(stderr);

  VkInstance instance = VK_NULL_HANDLE;
  VkResult result = create_instance(&create_info, NULL, &instance);

  fprintf(stderr, "MARK create-return result=%d callbacks=%u instance=%p\n", result, callback_count, (void *)instance);
  fflush(stderr);

  if (instance != VK_NULL_HANDLE) {
    PFN_vkDestroyInstance destroy_instance = (PFN_vkDestroyInstance)dlsym(vulkan, "vkDestroyInstance");
    if (destroy_instance) destroy_instance(instance, NULL);
  }

  if (callback_count == 0) {
    fprintf(stderr, "FAIL direct-driver callback not observed\n");
    return 10;
  }

  fprintf(stderr, "PASS callbacks=%u result=%d\n", callback_count, result);
  return 0;
}
