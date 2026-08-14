#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

struct Header { void *base; size_t size; };
struct Counters { volatile unsigned alloc_calls, realloc_calls, free_calls; int id; };
static struct Counters instance_counts = {.id = 1};
static struct Counters object_counts = {.id = 2};

static size_t norm_align(size_t a) {
  if (a < sizeof(void *)) a = sizeof(void *);
  size_t p = sizeof(void *);
  while (p < a && p <= SIZE_MAX / 2) p <<= 1;
  return p;
}

static struct Counters *get_counts(void *u) {
  if (u == &instance_counts) return &instance_counts;
  if (u == &object_counts) return &object_counts;
  _exit(90);
}

__attribute__((used,noinline)) static void *alloc_body(void *u, size_t size, size_t alignment, VkSystemAllocationScope scope) {
  (void)scope;
  struct Counters *c = get_counts(u);
  ++c->alloc_calls;
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
  struct Counters *c = get_counts(u);
  ++c->realloc_calls;
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
  struct Counters *c = get_counts(u);
  ++c->free_calls;
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

static void print_create(const char *name, VkResult r,
                         unsigned ia0, unsigned ir0, unsigned if0,
                         unsigned oa0, unsigned or0, unsigned of0) {
  fprintf(stderr,
          "%s result=%d instance_alloc_delta=%u instance_realloc_delta=%u instance_free_delta=%u object_alloc_delta=%u object_realloc_delta=%u object_free_delta=%u\n",
          name, r,
          instance_counts.alloc_calls-ia0, instance_counts.realloc_calls-ir0, instance_counts.free_calls-if0,
          object_counts.alloc_calls-oa0, object_counts.realloc_calls-or0, object_counts.free_calls-of0);
}

static void print_destroy(const char *name, unsigned if0, unsigned of0) {
  fprintf(stderr, "%s instance_free_delta=%u object_free_delta=%u\n",
          name, instance_counts.free_calls-if0, object_counts.free_calls-of0);
}

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

  VkAllocationCallbacks instance_cb = {
    .pUserData = &instance_counts,
    .pfnAllocation = allocation_cb,
    .pfnReallocation = reallocation_cb,
    .pfnFree = free_cb,
  };
  VkAllocationCallbacks object_cb = {
    .pUserData = &object_counts,
    .pfnAllocation = allocation_cb,
    .pfnReallocation = reallocation_cb,
    .pfnFree = free_cb,
  };
  VkApplicationInfo app = {
    .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
    .pApplicationName = "debug-dual-allocator-matrix",
    .apiVersion = VK_API_VERSION_1_0,
  };
  VkInstanceCreateInfo ici = {
    .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
    .pApplicationInfo = &app,
    .enabledExtensionCount = name_count,
    .ppEnabledExtensionNames = names,
  };
  VkInstance instance = VK_NULL_HANDLE;
  r = vkCreateInstance(&ici, &instance_cb, &instance);
  if (r != VK_SUCCESS) { fprintf(stderr, "FAIL instance create result=%d\n", r); return 5; }
  fprintf(stderr, "INSTANCE_CREATE totals=%u/%u/%u object_totals=%u/%u/%u\n",
          instance_counts.alloc_calls, instance_counts.realloc_calls, instance_counts.free_calls,
          object_counts.alloc_calls, object_counts.realloc_calls, object_counts.free_calls);

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
    unsigned ia0=instance_counts.alloc_calls, ir0=instance_counts.realloc_calls, if0=instance_counts.free_calls;
    unsigned oa0=object_counts.alloc_calls, or0=object_counts.realloc_calls, of0=object_counts.free_calls;
    r = create_utils(instance, &ci, &object_cb, &messenger);
    print_create("DEBUG_UTILS_CREATE", r, ia0, ir0, if0, oa0, or0, of0);
    if (r != VK_SUCCESS) return 7;
    if0=instance_counts.free_calls; of0=object_counts.free_calls;
    destroy_utils(instance, messenger, &object_cb);
    print_destroy("DEBUG_UTILS_DESTROY", if0, of0);
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
    unsigned ia0=instance_counts.alloc_calls, ir0=instance_counts.realloc_calls, if0=instance_counts.free_calls;
    unsigned oa0=object_counts.alloc_calls, or0=object_counts.realloc_calls, of0=object_counts.free_calls;
    r = create_report(instance, &ci, &object_cb, &callback);
    print_create("DEBUG_REPORT_CREATE", r, ia0, ir0, if0, oa0, or0, of0);
    if (r != VK_SUCCESS) return 9;
    if0=instance_counts.free_calls; of0=object_counts.free_calls;
    destroy_report(instance, callback, &object_cb);
    print_destroy("DEBUG_REPORT_DESTROY", if0, of0);
  }

  unsigned if0 = instance_counts.free_calls, of0 = object_counts.free_calls;
  vkDestroyInstance(instance, &instance_cb);
  fprintf(stderr,
          "INSTANCE_DESTROY instance_free_delta=%u object_free_delta=%u instance_totals=%u/%u/%u object_totals=%u/%u/%u\n",
          instance_counts.free_calls-if0, object_counts.free_calls-of0,
          instance_counts.alloc_calls, instance_counts.realloc_calls, instance_counts.free_calls,
          object_counts.alloc_calls, object_counts.realloc_calls, object_counts.free_calls);
  free(props);
  fprintf(stderr, "PASS debug dual allocator matrix\n");
  return 0;
}
