#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

struct Header { void *base; size_t size; };
static volatile unsigned alloc_calls;
static volatile unsigned realloc_calls;
static volatile unsigned free_calls;
static int cookie;

static size_t normalize_alignment(size_t a) {
  if (a < sizeof(void *)) a = sizeof(void *);
  size_t p = sizeof(void *);
  while (p < a && p <= SIZE_MAX / 2) p <<= 1;
  return p;
}

__attribute__((used,noinline)) static void *alloc_body(void *user, size_t size, size_t alignment, VkSystemAllocationScope scope) {
  if (user != &cookie) _exit(91);
  ++alloc_calls;
  alignment = normalize_alignment(alignment);
  if (size > SIZE_MAX - alignment - sizeof(struct Header)) return NULL;
  void *base = malloc(size + alignment + sizeof(struct Header));
  if (!base) return NULL;
  uintptr_t raw = (uintptr_t)base + sizeof(struct Header);
  uintptr_t aligned = (raw + alignment - 1) & ~(uintptr_t)(alignment - 1);
  struct Header *h = (struct Header *)(aligned - sizeof(struct Header));
  h->base = base;
  h->size = size;
  (void)scope;
  return (void *)aligned;
}

__attribute__((used,noinline)) static void *realloc_body(void *user, void *original, size_t size, size_t alignment, VkSystemAllocationScope scope) {
  if (user != &cookie) _exit(92);
  ++realloc_calls;
  if (!original) return alloc_body(user, size, alignment, scope);
  struct Header *oldh = (struct Header *)((uintptr_t)original - sizeof(struct Header));
  size_t old_size = oldh->size;
  if (size == 0) {
    free(oldh->base);
    return NULL;
  }
  void *p = alloc_body(user, size, alignment, scope);
  if (!p) return NULL;
  memcpy(p, original, old_size < size ? old_size : size);
  free(oldh->base);
  return p;
}

__attribute__((used,noinline)) static void free_body(void *user, void *memory) {
  if (user != &cookie) _exit(93);
  ++free_calls;
  if (!memory) return;
  struct Header *h = (struct Header *)((uintptr_t)memory - sizeof(struct Header));
  free(h->base);
}

#if defined(__x86_64__)
__attribute__((naked,noinline)) static void *VKAPI_CALL allocation_cb(void *u, size_t s, size_t a, VkSystemAllocationScope sc) {
  (void)u; (void)s; (void)a; (void)sc;
  __asm__ volatile(".byte 0xe9,0,0,0,0\n\tjmp alloc_body");
}
__attribute__((naked,noinline)) static void *VKAPI_CALL reallocation_cb(void *u, void *p, size_t s, size_t a, VkSystemAllocationScope sc) {
  (void)u; (void)p; (void)s; (void)a; (void)sc;
  __asm__ volatile(".byte 0xe9,0,0,0,0\n\tjmp realloc_body");
}
__attribute__((naked,noinline)) static void VKAPI_CALL free_cb(void *u, void *p) {
  (void)u; (void)p;
  __asm__ volatile(".byte 0xe9,0,0,0,0\n\tjmp free_body");
}
#else
static void *VKAPI_CALL allocation_cb(void *u, size_t s, size_t a, VkSystemAllocationScope sc) { return alloc_body(u,s,a,sc); }
static void *VKAPI_CALL reallocation_cb(void *u, void *p, size_t s, size_t a, VkSystemAllocationScope sc) { return realloc_body(u,p,s,a,sc); }
static void VKAPI_CALL free_cb(void *u, void *p) { free_body(u,p); }
#endif

#define LOAD(name) PFN_##name name = (PFN_##name)dlsym(h, #name)

int main(void) {
  void *h = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!h) { fprintf(stderr, "SKIP dlopen: %s\n", dlerror()); return 77; }
  LOAD(vkCreateInstance);
  LOAD(vkDestroyInstance);
  LOAD(vkEnumeratePhysicalDevices);
  LOAD(vkGetPhysicalDeviceQueueFamilyProperties);
  LOAD(vkCreateDevice);
  LOAD(vkDestroyDevice);
  LOAD(vkCreateBuffer);
  LOAD(vkDestroyBuffer);
  if (!vkCreateInstance || !vkDestroyInstance || !vkEnumeratePhysicalDevices ||
      !vkGetPhysicalDeviceQueueFamilyProperties || !vkCreateDevice || !vkDestroyDevice ||
      !vkCreateBuffer || !vkDestroyBuffer) {
    fprintf(stderr, "SKIP missing core symbol\n");
    return 77;
  }

  VkApplicationInfo app = {
    .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
    .pApplicationName = "fex-allocator-buffer-probe",
    .applicationVersion = 1,
    .pEngineName = "none",
    .engineVersion = 1,
    .apiVersion = VK_API_VERSION_1_0,
  };
  VkInstanceCreateInfo ici = {
    .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
    .pApplicationInfo = &app,
  };
  VkInstance instance = VK_NULL_HANDLE;
  VkResult r = vkCreateInstance(&ici, NULL, &instance);
  fprintf(stderr, "MARK instance result=%d instance=%p\n", r, (void*)instance);
  if (r != VK_SUCCESS) return 2;

  uint32_t nphys = 0;
  r = vkEnumeratePhysicalDevices(instance, &nphys, NULL);
  if (r != VK_SUCCESS || nphys == 0) return 3;
  VkPhysicalDevice *phys = calloc(nphys, sizeof(*phys));
  if (!phys) return 4;
  r = vkEnumeratePhysicalDevices(instance, &nphys, phys);
  if (r != VK_SUCCESS) return 5;

  uint32_t nq = 0;
  vkGetPhysicalDeviceQueueFamilyProperties(phys[0], &nq, NULL);
  if (!nq) return 6;
  VkQueueFamilyProperties *qprops = calloc(nq, sizeof(*qprops));
  if (!qprops) return 7;
  vkGetPhysicalDeviceQueueFamilyProperties(phys[0], &nq, qprops);
  uint32_t family = 0;
  for (; family < nq; ++family) {
    if (qprops[family].queueCount) break;
  }
  if (family == nq) return 8;

  float priority = 1.0f;
  VkDeviceQueueCreateInfo qci = {
    .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
    .queueFamilyIndex = family,
    .queueCount = 1,
    .pQueuePriorities = &priority,
  };
  VkDeviceCreateInfo dci = {
    .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
    .queueCreateInfoCount = 1,
    .pQueueCreateInfos = &qci,
  };
  VkDevice device = VK_NULL_HANDLE;
  r = vkCreateDevice(phys[0], &dci, NULL, &device);
  fprintf(stderr, "MARK device result=%d device=%p family=%u\n", r, (void*)device, family);
  if (r != VK_SUCCESS) return 9;

  VkAllocationCallbacks callbacks = {
    .pUserData = &cookie,
    .pfnAllocation = allocation_cb,
    .pfnReallocation = reallocation_cb,
    .pfnFree = free_cb,
    .pfnInternalAllocation = NULL,
    .pfnInternalFree = NULL,
  };
  VkBufferCreateInfo bci = {
    .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
    .size = 4096,
    .usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
    .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
  };
  VkBuffer buffer = VK_NULL_HANDLE;
  fprintf(stderr, "MARK create-buffer-enter guest_alloc=%p\n", (void*)callbacks.pfnAllocation);
  fflush(stderr);
  r = vkCreateBuffer(device, &bci, &callbacks, &buffer);
  fprintf(stderr, "MARK create-buffer-return result=%d buffer=%llu alloc=%u realloc=%u free=%u\n",
          r, (unsigned long long)buffer, alloc_calls, realloc_calls, free_calls);
  fflush(stderr);
  if (r != VK_SUCCESS) return 10;

  fprintf(stderr, "MARK destroy-buffer-enter\n");
  fflush(stderr);
  vkDestroyBuffer(device, buffer, &callbacks);
  fprintf(stderr, "MARK destroy-buffer-return alloc=%u realloc=%u free=%u\n", alloc_calls, realloc_calls, free_calls);
  fflush(stderr);

  vkDestroyDevice(device, NULL);
  vkDestroyInstance(instance, NULL);
  free(qprops);
  free(phys);

  if (alloc_calls == 0 || free_calls == 0) {
    fprintf(stderr, "FAIL allocator callbacks not observed\n");
    return 11;
  }
  fprintf(stderr, "PASS allocator callbacks observed\n");
  return 0;
}
