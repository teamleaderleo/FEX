#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

struct Header { void *base; size_t size; };
static volatile unsigned alloc_calls, realloc_calls, free_calls;
static int cookie;

static size_t norm_align(size_t a) {
  if (a < sizeof(void *)) a = sizeof(void *);
  size_t p = sizeof(void *);
  while (p < a && p <= SIZE_MAX / 2) p <<= 1;
  return p;
}

__attribute__((used,noinline)) static void *alloc_body(void *u, size_t size, size_t alignment, VkSystemAllocationScope scope) {
  (void)scope;
  if (u != &cookie) _exit(91);
  ++alloc_calls;
  alignment = norm_align(alignment);
  if (size > SIZE_MAX - alignment - sizeof(struct Header)) return NULL;
  void *base = malloc(size + alignment + sizeof(struct Header));
  if (!base) return NULL;
  uintptr_t raw = (uintptr_t)base + sizeof(struct Header);
  uintptr_t aligned = (raw + alignment - 1) & ~(uintptr_t)(alignment - 1);
  struct Header *h = (struct Header *)(aligned - sizeof(*h));
  h->base = base; h->size = size;
  return (void *)aligned;
}

__attribute__((used,noinline)) static void *realloc_body(void *u, void *old, size_t size, size_t alignment, VkSystemAllocationScope scope) {
  if (u != &cookie) _exit(92);
  ++realloc_calls;
  if (!old) return alloc_body(u, size, alignment, scope);
  struct Header *h = (struct Header *)((uintptr_t)old - sizeof(*h));
  size_t old_size = h->size;
  if (!size) { free(h->base); return NULL; }
  void *p = alloc_body(u, size, alignment, scope);
  if (!p) return NULL;
  memcpy(p, old, old_size < size ? old_size : size);
  free(h->base);
  return p;
}

__attribute__((used,noinline)) static void free_body(void *u, void *p) {
  if (u != &cookie) _exit(93);
  ++free_calls;
  if (!p) return;
  struct Header *h = (struct Header *)((uintptr_t)p - sizeof(*h));
  free(h->base);
}

#if defined(__x86_64__)
__attribute__((naked,noinline)) static void *allocation_cb(void *u, size_t s, size_t a, VkSystemAllocationScope sc) {
  (void)u; (void)s; (void)a; (void)sc;
  __asm__ volatile(".byte 0xe9,0,0,0,0\n\tjmp alloc_body");
}
__attribute__((naked,noinline)) static void *reallocation_cb(void *u, void *p, size_t s, size_t a, VkSystemAllocationScope sc) {
  (void)u; (void)p; (void)s; (void)a; (void)sc;
  __asm__ volatile(".byte 0xe9,0,0,0,0\n\tjmp realloc_body");
}
__attribute__((naked,noinline)) static void free_cb(void *u, void *p) {
  (void)u; (void)p;
  __asm__ volatile(".byte 0xe9,0,0,0,0\n\tjmp free_body");
}
#else
static void *allocation_cb(void *u, size_t s, size_t a, VkSystemAllocationScope sc) { return alloc_body(u,s,a,sc); }
static void *reallocation_cb(void *u, void *p, size_t s, size_t a, VkSystemAllocationScope sc) { return realloc_body(u,p,s,a,sc); }
static void free_cb(void *u, void *p) { free_body(u,p); }
#endif

static VKAPI_ATTR VkBool32 VKAPI_CALL debug_utils_cb(
    VkDebugUtilsMessageSeverityFlagBitsEXT severity,
    VkDebugUtilsMessageTypeFlagsEXT types,
    const VkDebugUtilsMessengerCallbackDataEXT *data,
    void *user) {
  (void)severity; (void)types; (void)data; (void)user;
  return VK_FALSE;
}

static VKAPI_ATTR VkBool32 VKAPI_CALL debug_report_cb(
    VkDebugReportFlagsEXT flags,
    VkDebugReportObjectTypeEXT objectType,
    uint64_t object,
    size_t location,
    int32_t messageCode,
    const char *layerPrefix,
    const char *message,
    void *user) {
  (void)flags; (void)objectType; (void)object; (void)location; (void)messageCode;
  (void)layerPrefix; (void)message; (void)user;
  return VK_FALSE;
}

static int has_extension(const VkExtensionProperties *props, uint32_t count, const char *name) {
  for (uint32_t i = 0; i < count; ++i) {
    if (strcmp(props[i].extensionName, name) == 0) return 1;
  }
  return 0;
}

#define LOAD(name) PFN_##name name = (PFN_##name)dlsym(h, #name)

int main(void) {
  void *h = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!h) { fprintf(stderr, "SKIP dlopen: %s\n", dlerror()); return 77; }
  LOAD(vkEnumerateInstanceExtensionProperties);
  LOAD(vkCreateInstance);
  LOAD(vkDestroyInstance);
  LOAD(vkGetInstanceProcAddr);
  if (!vkEnumerateInstanceExtensionProperties || !vkCreateInstance || !vkDestroyInstance || !vkGetInstanceProcAddr) return 77;

  uint32_t ext_count = 0;
  VkResult r = vkEnumerateInstanceExtensionProperties(NULL, &ext_count, NULL);
  if (r != VK_SUCCESS || !ext_count) return 2;
  VkExtensionProperties *props = calloc(ext_count, sizeof(*props));
  if (!props) return 3;
  r = vkEnumerateInstanceExtensionProperties(NULL, &ext_count, props);
  if (r != VK_SUCCESS) return 4;

  int have_utils = has_extension(props, ext_count, VK_EXT_DEBUG_UTILS_EXTENSION_NAME);
  int have_report = has_extension(props, ext_count, VK_EXT_DEBUG_REPORT_EXTENSION_NAME);
  fprintf(stderr, "EXTENSIONS debug_utils=%d debug_report=%d\n", have_utils, have_report);
  if (!have_utils && !have_report) { free(props); return 77; }

  const char *names[2];
  uint32_t name_count = 0;
  if (have_utils) names[name_count++] = VK_EXT_DEBUG_UTILS_EXTENSION_NAME;
  if (have_report) names[name_count++] = VK_EXT_DEBUG_REPORT_EXTENSION_NAME;

  VkAllocationCallbacks cb = {
    .pUserData = &cookie,
    .pfnAllocation = allocation_cb,
    .pfnReallocation = reallocation_cb,
    .pfnFree = free_cb,
  };
  VkApplicationInfo app = {
    .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
    .pApplicationName = "debug-allocator-matrix",
    .apiVersion = VK_API_VERSION_1_0,
  };
  VkInstanceCreateInfo ici = {
    .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
    .pApplicationInfo = &app,
    .enabledExtensionCount = name_count,
    .ppEnabledExtensionNames = names,
  };
  VkInstance instance = VK_NULL_HANDLE;
  r = vkCreateInstance(&ici, &cb, &instance);
  if (r != VK_SUCCESS) { fprintf(stderr, "FAIL instance create result=%d\n", r); return 5; }

  unsigned exercised = 0;
  unsigned callbacks_seen = 0;

  if (have_utils) {
    PFN_vkCreateDebugUtilsMessengerEXT create_utils = (PFN_vkCreateDebugUtilsMessengerEXT)vkGetInstanceProcAddr(instance, "vkCreateDebugUtilsMessengerEXT");
    PFN_vkDestroyDebugUtilsMessengerEXT destroy_utils = (PFN_vkDestroyDebugUtilsMessengerEXT)vkGetInstanceProcAddr(instance, "vkDestroyDebugUtilsMessengerEXT");
    if (!create_utils || !destroy_utils) return 6;
    VkDebugUtilsMessengerCreateInfoEXT ci = {
      .sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT,
      .messageSeverity = VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT,
      .messageType = VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_TYPE_PERFORMANCE_BIT_EXT,
      .pfnUserCallback = debug_utils_cb,
    };
    VkDebugUtilsMessengerEXT messenger = VK_NULL_HANDLE;
    unsigned a0 = alloc_calls, r0 = realloc_calls, f0 = free_calls;
    r = create_utils(instance, &ci, &cb, &messenger);
    fprintf(stderr, "DEBUG_UTILS_CREATE result=%d alloc_delta=%u realloc_delta=%u free_delta=%u\n",
            r, alloc_calls-a0, realloc_calls-r0, free_calls-f0);
    if (r != VK_SUCCESS) return 7;
    if ((alloc_calls-a0) || (realloc_calls-r0) || (free_calls-f0)) ++callbacks_seen;
    f0 = free_calls;
    destroy_utils(instance, messenger, &cb);
    fprintf(stderr, "DEBUG_UTILS_DESTROY free_delta=%u\n", free_calls-f0);
    if (free_calls-f0) ++callbacks_seen;
    ++exercised;
  }

  if (have_report) {
    PFN_vkCreateDebugReportCallbackEXT create_report = (PFN_vkCreateDebugReportCallbackEXT)vkGetInstanceProcAddr(instance, "vkCreateDebugReportCallbackEXT");
    PFN_vkDestroyDebugReportCallbackEXT destroy_report = (PFN_vkDestroyDebugReportCallbackEXT)vkGetInstanceProcAddr(instance, "vkDestroyDebugReportCallbackEXT");
    if (!create_report || !destroy_report) return 8;
    VkDebugReportCallbackCreateInfoEXT ci = {
      .sType = VK_STRUCTURE_TYPE_DEBUG_REPORT_CALLBACK_CREATE_INFO_EXT,
      .flags = VK_DEBUG_REPORT_WARNING_BIT_EXT | VK_DEBUG_REPORT_ERROR_BIT_EXT,
      .pfnCallback = debug_report_cb,
    };
    VkDebugReportCallbackEXT callback = VK_NULL_HANDLE;
    unsigned a0 = alloc_calls, r0 = realloc_calls, f0 = free_calls;
    r = create_report(instance, &ci, &cb, &callback);
    fprintf(stderr, "DEBUG_REPORT_CREATE result=%d alloc_delta=%u realloc_delta=%u free_delta=%u\n",
            r, alloc_calls-a0, realloc_calls-r0, free_calls-f0);
    if (r != VK_SUCCESS) return 9;
    if ((alloc_calls-a0) || (realloc_calls-r0) || (free_calls-f0)) ++callbacks_seen;
    f0 = free_calls;
    destroy_report(instance, callback, &cb);
    fprintf(stderr, "DEBUG_REPORT_DESTROY free_delta=%u\n", free_calls-f0);
    if (free_calls-f0) ++callbacks_seen;
    ++exercised;
  }

  unsigned f0 = free_calls;
  vkDestroyInstance(instance, &cb);
  fprintf(stderr, "INSTANCE_DESTROY free_delta=%u totals=%u/%u/%u exercised=%u callbacks_seen=%u\n",
          free_calls-f0, alloc_calls, realloc_calls, free_calls, exercised, callbacks_seen);
  free(props);
  if (!exercised) return 10;
  fprintf(stderr, "PASS debug allocator extension matrix\n");
  return 0;
}
