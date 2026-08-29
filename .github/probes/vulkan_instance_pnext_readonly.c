#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

static unsigned report_callbacks;
static unsigned utils_callbacks;

static VKAPI_ATTR VkBool32 VKAPI_CALL report_callback(VkDebugReportFlagsEXT flags, VkDebugReportObjectTypeEXT object_type,
                                                       uint64_t object, size_t location, int32_t code, const char* layer_prefix,
                                                       const char* message, void* user_data) {
  (void)flags;
  (void)object_type;
  (void)object;
  (void)location;
  (void)code;
  (void)layer_prefix;
  (void)message;
  (void)user_data;
  ++report_callbacks;
  return VK_FALSE;
}

static VKAPI_ATTR VkBool32 VKAPI_CALL utils_callback(VkDebugUtilsMessageSeverityFlagBitsEXT severity,
                                                      VkDebugUtilsMessageTypeFlagsEXT types,
                                                      const VkDebugUtilsMessengerCallbackDataEXT* callback_data, void* user_data) {
  (void)severity;
  (void)types;
  (void)callback_data;
  (void)user_data;
  ++utils_callbacks;
  return VK_FALSE;
}

static void* page_alloc(size_t page_size) {
  void* page = mmap(NULL, page_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (page == MAP_FAILED) {
    perror("mmap");
    exit(90);
  }
  return page;
}

int main(void) {
  const size_t page_size = (size_t)sysconf(_SC_PAGESIZE);
  VkInstanceCreateInfo* instance_info = page_alloc(page_size);
  VkDebugReportCallbackCreateInfoEXT* report_info = page_alloc(page_size);
  VkValidationFeaturesEXT* validation_info = page_alloc(page_size);
  VkDebugUtilsMessengerCreateInfoEXT* utils_info = page_alloc(page_size);

  *utils_info = (VkDebugUtilsMessengerCreateInfoEXT) {
    .sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT,
    .messageSeverity = VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT,
    .messageType = VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT,
    .pfnUserCallback = utils_callback,
    .pUserData = (void*)(uintptr_t)0x5555666677778888ULL,
  };
  *validation_info = (VkValidationFeaturesEXT) {
    .sType = VK_STRUCTURE_TYPE_VALIDATION_FEATURES_EXT,
    .pNext = utils_info,
  };
  *report_info = (VkDebugReportCallbackCreateInfoEXT) {
    .sType = VK_STRUCTURE_TYPE_DEBUG_REPORT_CALLBACK_CREATE_INFO_EXT,
    .pNext = validation_info,
    .flags = VK_DEBUG_REPORT_WARNING_BIT_EXT | VK_DEBUG_REPORT_ERROR_BIT_EXT,
    .pfnCallback = report_callback,
    .pUserData = (void*)(uintptr_t)0x1111222233334444ULL,
  };

  const char* layers[] = {"VK_LAYER_KHRONOS_validation"};
  const char* extensions[] = {
    VK_EXT_DEBUG_REPORT_EXTENSION_NAME,
    VK_EXT_DEBUG_UTILS_EXTENSION_NAME,
    VK_EXT_VALIDATION_FEATURES_EXTENSION_NAME,
  };
  *instance_info = (VkInstanceCreateInfo) {
    .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
    .pNext = report_info,
    .flags = (VkInstanceCreateFlags)0x80000000U,
    .enabledLayerCount = 1,
    .ppEnabledLayerNames = layers,
    .enabledExtensionCount = sizeof(extensions) / sizeof(extensions[0]),
    .ppEnabledExtensionNames = extensions,
  };

  const VkInstanceCreateInfo instance_before = *instance_info;
  const VkDebugReportCallbackCreateInfoEXT report_before = *report_info;
  const VkValidationFeaturesEXT validation_before = *validation_info;
  const VkDebugUtilsMessengerCreateInfoEXT utils_before = *utils_info;

  if (mprotect(instance_info, page_size, PROT_READ) || mprotect(report_info, page_size, PROT_READ) ||
      mprotect(validation_info, page_size, PROT_READ) || mprotect(utils_info, page_size, PROT_READ)) {
    perror("mprotect");
    return 91;
  }

  void* vulkan = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!vulkan) {
    fprintf(stderr, "PROBE_DLOPEN_ERROR %s\n", dlerror());
    return 92;
  }
  PFN_vkCreateInstance create_instance = (PFN_vkCreateInstance)dlsym(vulkan, "vkCreateInstance");
  PFN_vkDestroyInstance destroy_instance = (PFN_vkDestroyInstance)dlsym(vulkan, "vkDestroyInstance");
  if (!create_instance || !destroy_instance) {
    fprintf(stderr, "PROBE_DLSYM_ERROR\n");
    return 93;
  }

  fprintf(stderr, "PROBE_BEFORE_CREATE callbacks=%u/%u\n", report_callbacks, utils_callbacks);
  VkInstance instance = VK_NULL_HANDLE;
  VkResult result = create_instance(instance_info, NULL, &instance);
  const int unchanged = memcmp(instance_info, &instance_before, sizeof(instance_before)) == 0 &&
                        memcmp(report_info, &report_before, sizeof(report_before)) == 0 &&
                        memcmp(validation_info, &validation_before, sizeof(validation_before)) == 0 &&
                        memcmp(utils_info, &utils_before, sizeof(utils_before)) == 0;
  fprintf(stderr, "PROBE_AFTER_CREATE result=%d instance=%p callbacks=%u/%u unchanged=%d\n", result, (void*)instance,
          report_callbacks, utils_callbacks, unchanged);

  if (instance) {
    destroy_instance(instance, NULL);
  }
  dlclose(vulkan);

  munmap(instance_info, page_size);
  munmap(report_info, page_size);
  munmap(validation_info, page_size);
  munmap(utils_info, page_size);

  if (result != VK_SUCCESS || !instance || !unchanged) {
    return 40;
  }
  fprintf(stderr, "PROBE_RETURN callbacks=%u/%u unchanged=%d\n", report_callbacks, utils_callbacks, unchanged);
  return 0;
}
