#define _GNU_SOURCE
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static VKAPI_ATTR VkBool32 VKAPI_CALL debug_report_cb(VkDebugReportFlagsEXT flags,
                                                       VkDebugReportObjectTypeEXT objectType,
                                                       uint64_t object,
                                                       size_t location,
                                                       int32_t messageCode,
                                                       const char *layerPrefix,
                                                       const char *message,
                                                       void *userData) {
  (void)flags; (void)objectType; (void)object; (void)location; (void)messageCode;
  (void)layerPrefix; (void)message; (void)userData;
  return VK_FALSE;
}

int main(void) {
  void *vk = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!vk) { fprintf(stderr, "SKIP dlopen: %s\n", dlerror()); return 77; }
  PFN_vkCreateInstance create_instance = (PFN_vkCreateInstance)dlsym(vk, "vkCreateInstance");
  if (!create_instance) return 77;

  const long page_size = sysconf(_SC_PAGESIZE);
  if (page_size <= 0) return 70;
  void *page = mmap(NULL, (size_t)page_size, PROT_READ | PROT_WRITE,
                    MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (page == MAP_FAILED) { perror("mmap"); return 71; }

  VkDebugReportCallbackCreateInfoEXT debug = {
    .sType = VK_STRUCTURE_TYPE_DEBUG_REPORT_CALLBACK_CREATE_INFO_EXT,
    .pNext = NULL,
    .flags = VK_DEBUG_REPORT_ERROR_BIT_EXT | VK_DEBUG_REPORT_WARNING_BIT_EXT,
    .pfnCallback = debug_report_cb,
    .pUserData = NULL,
  };
  VkApplicationInfo app = {
    .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
    .pApplicationName = "fex-readonly-pnext",
    .apiVersion = VK_API_VERSION_1_0,
  };
  const char *extensions[] = {
    VK_EXT_DEBUG_REPORT_EXTENSION_NAME,
    "VK_FEX_intentionally_missing_extension",
  };
  VkInstanceCreateInfo *info = (VkInstanceCreateInfo *)page;
  *info = (VkInstanceCreateInfo) {
    .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
    .pNext = &debug,
    .pApplicationInfo = &app,
    .enabledExtensionCount = 2,
    .ppEnabledExtensionNames = extensions,
  };
  const void *original_pnext = info->pNext;

  if (mprotect(page, (size_t)page_size, PROT_READ) != 0) {
    perror("mprotect"); return 72;
  }

  fprintf(stderr, "MARK readonly=%p pnext=%p\n", page, original_pnext);
  fprintf(stderr, "MARK create-enter\n");
  fflush(stderr);
  VkInstance instance = VK_NULL_HANDLE;
  VkResult result = create_instance(info, NULL, &instance);
  fprintf(stderr, "MARK create-return result=%d instance=%p pnext=%p same=%d\n",
          result, (void *)instance, info->pNext, info->pNext == original_pnext);
  fflush(stderr);

  if (instance != VK_NULL_HANDLE) {
    PFN_vkDestroyInstance destroy_instance = (PFN_vkDestroyInstance)dlsym(vk, "vkDestroyInstance");
    if (destroy_instance) destroy_instance(instance, NULL);
  }

  return info->pNext == original_pnext ? 0 : 20;
}
