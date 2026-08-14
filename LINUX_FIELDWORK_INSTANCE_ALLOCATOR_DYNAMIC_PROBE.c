#define main allocator_probe_original_main
#include "investigations/fex-vulkan-thunk-lifecycle/agent-b/vk_allocator_instance_probe.c"
#undef main

typedef PFN_vkVoidFunction (*PFN_vkGetInstanceProcAddr)(VkInstance, const char *);

int main(void) {
  void *h = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!h) {
    fprintf(stderr, "SKIP dlopen: %s\n", dlerror());
    return 77;
  }

  PFN_vkCreateInstance create = (PFN_vkCreateInstance)dlsym(h, "vkCreateInstance");
  PFN_vkGetInstanceProcAddr gipa = (PFN_vkGetInstanceProcAddr)dlsym(h, "vkGetInstanceProcAddr");
  if (!create || !gipa) {
    fprintf(stderr, "SKIP core symbols\n");
    return 77;
  }

  VkAllocationCallbacks callbacks = {
    .pUserData = &cookie,
    .pfnAllocation = allocation_cb,
    .pfnReallocation = reallocation_cb,
    .pfnFree = free_cb,
    .pfnInternalAllocation = NULL,
    .pfnInternalFree = NULL,
  };
  VkApplicationInfo app = {
    .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
    .pApplicationName = "fex-vulkan-allocator-dynamic-probe",
    .applicationVersion = 1,
    .pEngineName = "none",
    .engineVersion = 1,
    .apiVersion = VK_API_VERSION_1_0,
  };
  VkInstanceCreateInfo ci = {
    .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
    .pApplicationInfo = &app,
  };

  fprintf(stderr, "CASE allocator destroy_route=gipa\n");
  fprintf(stderr, "MARK create-enter\n");
  fflush(stderr);
  VkInstance instance = NULL;
  VkResult r = create(&ci, &callbacks, &instance);
  fprintf(stderr, "MARK create-return result=%d instance=%p alloc=%u realloc=%u free=%u\n",
          r, (void *)instance, alloc_calls, realloc_calls, free_calls);
  fflush(stderr);
  if (r != VK_SUCCESS) return 2;

  PFN_vkDestroyInstance destroy = (PFN_vkDestroyInstance)gipa(instance, "vkDestroyInstance");
  fprintf(stderr, "MARK gipa-destroy ptr=%p\n", (void *)destroy);
  fflush(stderr);
  if (!destroy) return 3;

  unsigned before_destroy = free_calls;
  fprintf(stderr, "MARK destroy-enter\n");
  fflush(stderr);
  destroy(instance, &callbacks);
  fprintf(stderr, "MARK destroy-return alloc=%u realloc=%u free=%u free_delta=%u\n",
          alloc_calls, realloc_calls, free_calls, free_calls - before_destroy);
  fflush(stderr);

  if (alloc_calls == 0 || free_calls == 0) {
    fprintf(stderr, "FAIL allocator callbacks not observed\n");
    return 10;
  }
  fprintf(stderr, "PASS allocator callbacks observed\n");
  return 0;
}
