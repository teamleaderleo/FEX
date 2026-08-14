#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

static volatile unsigned alloc_calls;
static volatile unsigned free_calls;
static int cookie;

static size_t normalize_alignment(size_t a) {
  if (a < sizeof(void *)) a = sizeof(void *);
  size_t p = sizeof(void *);
  while (p < a && p <= SIZE_MAX / 2) p <<= 1;
  return p;
}

struct Header { void *base; };

__attribute__((used,noinline)) static void *alloc_body(void *user, size_t size, size_t alignment, VkSystemAllocationScope scope) {
  if (user != &cookie) _exit(91);
  ++alloc_calls;
  alignment = normalize_alignment(alignment);
  if (size > SIZE_MAX - alignment - sizeof(struct Header)) return NULL;
  void *base = malloc(size + alignment + sizeof(struct Header));
  if (!base) return NULL;
  uintptr_t raw = (uintptr_t)base + sizeof(struct Header);
  uintptr_t aligned = (raw + alignment - 1) & ~(uintptr_t)(alignment - 1);
  ((struct Header *)(aligned - sizeof(struct Header)))->base = base;
  fprintf(stderr, "EVENT_ALLOC ptr=%p size=%zu align=%zu scope=%u\n", (void *)aligned, size, alignment, (unsigned)scope);
  fflush(stderr);
  return (void *)aligned;
}

__attribute__((used,noinline)) static void free_body(void *user, void *memory) {
  if (user != &cookie) _exit(92);
  ++free_calls;
  fprintf(stderr, "EVENT_FREE_ENTER ptr=%p count=%u\n", memory, free_calls);
  fflush(stderr);
  if (memory) {
    struct Header *h = (struct Header *)((uintptr_t)memory - sizeof(struct Header));
    free(h->base);
  }
  fprintf(stderr, "EVENT_FREE_RETURN ptr=%p\n", memory);
  fflush(stderr);
}

#if defined(__x86_64__)
__attribute__((naked,noinline)) static void *VKAPI_CALL allocation_cb(void *u, size_t s, size_t a, VkSystemAllocationScope sc) {
  (void)u; (void)s; (void)a; (void)sc;
  __asm__ volatile("jmp alloc_body");
}
__attribute__((naked,noinline)) static void VKAPI_CALL free_cb(void *u, void *p) {
  (void)u; (void)p;
  __asm__ volatile("jmp free_body");
}
#else
static void *VKAPI_CALL allocation_cb(void *u, size_t s, size_t a, VkSystemAllocationScope sc) { return alloc_body(u, s, a, sc); }
static void VKAPI_CALL free_cb(void *u, void *p) { free_body(u, p); }
#endif

#define LOAD(name) PFN_##name name = (PFN_##name)dlsym(h, #name)

int main(void) {
  void *h = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!h) return 77;
  LOAD(vkCreateInstance); LOAD(vkDestroyInstance); LOAD(vkEnumeratePhysicalDevices);
  LOAD(vkGetPhysicalDeviceQueueFamilyProperties); LOAD(vkCreateDevice); LOAD(vkDestroyDevice);
  LOAD(vkCreateEvent); LOAD(vkDestroyEvent);
  if (!vkCreateInstance || !vkDestroyInstance || !vkEnumeratePhysicalDevices || !vkGetPhysicalDeviceQueueFamilyProperties ||
      !vkCreateDevice || !vkDestroyDevice || !vkCreateEvent || !vkDestroyEvent) return 77;

  VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "allocator-event-probe", .apiVersion = VK_API_VERSION_1_0};
  VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app};
  VkInstance instance = VK_NULL_HANDLE;
  VkResult r = vkCreateInstance(&ici, NULL, &instance);
  if (r != VK_SUCCESS) return 2;

  uint32_t nphys = 0;
  r = vkEnumeratePhysicalDevices(instance, &nphys, NULL);
  if (r != VK_SUCCESS || !nphys) return 3;
  VkPhysicalDevice *phys = calloc(nphys, sizeof(*phys));
  if (!phys) return 4;
  r = vkEnumeratePhysicalDevices(instance, &nphys, phys);
  if (r != VK_SUCCESS) return 5;

  uint32_t nq = 0;
  vkGetPhysicalDeviceQueueFamilyProperties(phys[0], &nq, NULL);
  if (!nq) return 6;
  VkQueueFamilyProperties *qp = calloc(nq, sizeof(*qp));
  if (!qp) return 7;
  vkGetPhysicalDeviceQueueFamilyProperties(phys[0], &nq, qp);
  uint32_t family = 0;
  while (family < nq && !qp[family].queueCount) ++family;
  if (family == nq) return 8;

  float pri = 1.0f;
  VkDeviceQueueCreateInfo qci = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &pri};
  VkDeviceCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1, .pQueueCreateInfos = &qci};
  VkDevice device = VK_NULL_HANDLE;
  r = vkCreateDevice(phys[0], &dci, NULL, &device);
  if (r != VK_SUCCESS) return 9;

  VkAllocationCallbacks cb = {
    .pUserData = &cookie,
    .pfnAllocation = allocation_cb,
    .pfnReallocation = NULL,
    .pfnFree = free_cb,
    .pfnInternalAllocation = NULL,
    .pfnInternalFree = NULL,
  };
  VkEventCreateInfo eci = {.sType = VK_STRUCTURE_TYPE_EVENT_CREATE_INFO};
  VkEvent event = VK_NULL_HANDLE;
  fprintf(stderr, "EVENT_CREATE_ENTER\n"); fflush(stderr);
  r = vkCreateEvent(device, &eci, &cb, &event);
  fprintf(stderr, "EVENT_CREATE_RETURN result=%d event=%llu alloc=%u free=%u\n", r, (unsigned long long)event, alloc_calls, free_calls); fflush(stderr);
  if (r != VK_SUCCESS) return 10;
  fprintf(stderr, "EVENT_DESTROY_ENTER event=%llu\n", (unsigned long long)event); fflush(stderr);
  vkDestroyEvent(device, event, &cb);
  fprintf(stderr, "EVENT_DESTROY_RETURN alloc=%u free=%u\n", alloc_calls, free_calls); fflush(stderr);

  vkDestroyDevice(device, NULL);
  vkDestroyInstance(instance, NULL);
  free(qp);
  free(phys);
  if (alloc_calls == 0 || free_calls == 0) return 11;
  fprintf(stderr, "PASS event allocator lifetime\n");
  return 0;
}
