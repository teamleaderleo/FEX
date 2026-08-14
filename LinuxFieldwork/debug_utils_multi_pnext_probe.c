#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <stdio.h>
#include <unistd.h>

static volatile unsigned callback_count;

__attribute__((used,noinline)) static VkBool32 VKAPI_PTR callback_body(VkDebugUtilsMessageSeverityFlagBitsEXT severity,
                                                                        VkDebugUtilsMessageTypeFlagsEXT types,
                                                                        const VkDebugUtilsMessengerCallbackDataEXT *data,
                                                                        void *user) {
  (void)severity;
  (void)types;
  (void)user;
  ++callback_count;
  fprintf(stderr, "CALLBACK count=%u id=%s\n", callback_count,
          data && data->pMessageIdName ? data->pMessageIdName : "(null)");
  fflush(stderr);
  return VK_FALSE;
}

#if defined(__x86_64__)
__attribute__((naked,noinline)) static VkBool32 VKAPI_PTR callback(VkDebugUtilsMessageSeverityFlagBitsEXT severity,
                                                                   VkDebugUtilsMessageTypeFlagsEXT types,
                                                                   const VkDebugUtilsMessengerCallbackDataEXT *data,
                                                                   void *user) {
  (void)severity;
  (void)types;
  (void)data;
  (void)user;
  __asm__ volatile(".byte 0xe9,0,0,0,0\n\tjmp callback_body");
}
#else
static VkBool32 VKAPI_PTR callback(VkDebugUtilsMessageSeverityFlagBitsEXT severity,
                                   VkDebugUtilsMessageTypeFlagsEXT types,
                                   const VkDebugUtilsMessengerCallbackDataEXT *data,
                                   void *user) {
  return callback_body(severity, types, data, user);
}
#endif

int main(void) {
  void *vulkan = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!vulkan) return 2;
  PFN_vkCreateInstance create_instance = (PFN_vkCreateInstance)dlsym(vulkan, "vkCreateInstance");
  if (!create_instance) return 3;

  VkDebugUtilsMessengerCreateInfoEXT second = {
    .sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT,
    .pNext = NULL,
    .flags = 0,
    .messageSeverity = VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT,
    .messageType = VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT,
    .pfnUserCallback = callback,
    .pUserData = NULL,
  };
  VkDebugUtilsMessengerCreateInfoEXT first = second;
  first.pNext = &second;

  const char *layers[] = {"VK_LAYER_KHRONOS_validation"};
  const char *extensions[] = {VK_EXT_DEBUG_UTILS_EXTENSION_NAME, "VK_FEX_intentionally_missing_extension"};
  VkApplicationInfo app = {
    .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
    .pApplicationName = "fex-debug-utils-multi-pnext",
    .apiVersion = VK_API_VERSION_1_0,
  };
  VkInstanceCreateInfo info = {
    .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
    .pNext = &first,
    .pApplicationInfo = &app,
    .enabledLayerCount = 1,
    .ppEnabledLayerNames = layers,
    .enabledExtensionCount = 2,
    .ppEnabledExtensionNames = extensions,
  };

  fprintf(stderr, "CALLBACK_PTR=%p FIRST=%p SECOND=%p\n", (void *)callback, (void *)&first, (void *)&second);
  fprintf(stderr, "MARK create-enter\n");
  fflush(stderr);
  VkInstance instance = VK_NULL_HANDLE;
  VkResult result = create_instance(&info, NULL, &instance);
  fprintf(stderr, "MARK create-return result=%d callbacks=%u instance=%p\n", result, callback_count, (void *)instance);
  fflush(stderr);
  _exit(callback_count > 0 ? 0 : 20);
}
