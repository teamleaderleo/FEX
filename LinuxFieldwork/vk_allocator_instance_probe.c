#define _GNU_SOURCE
#include <dlfcn.h>
#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef uint32_t VkFlags;
typedef uint32_t VkBool32;
typedef int32_t VkResult;
typedef uint32_t VkStructureType;
typedef struct VkInstance_T *VkInstance;
typedef void (*PFN_vkVoidFunction)(void);
typedef enum VkSystemAllocationScope {
  VK_SYSTEM_ALLOCATION_SCOPE_COMMAND = 0,
  VK_SYSTEM_ALLOCATION_SCOPE_OBJECT = 1,
  VK_SYSTEM_ALLOCATION_SCOPE_CACHE = 2,
  VK_SYSTEM_ALLOCATION_SCOPE_DEVICE = 3,
  VK_SYSTEM_ALLOCATION_SCOPE_INSTANCE = 4,
} VkSystemAllocationScope;
typedef enum VkInternalAllocationType {
  VK_INTERNAL_ALLOCATION_TYPE_EXECUTABLE = 0,
} VkInternalAllocationType;

typedef void *(*PFN_vkAllocationFunction)(void *, size_t, size_t, VkSystemAllocationScope);
typedef void *(*PFN_vkReallocationFunction)(void *, void *, size_t, size_t, VkSystemAllocationScope);
typedef void (*PFN_vkFreeFunction)(void *, void *);
typedef void (*PFN_vkInternalAllocationNotification)(void *, size_t, VkInternalAllocationType, VkSystemAllocationScope);
typedef void (*PFN_vkInternalFreeNotification)(void *, size_t, VkInternalAllocationType, VkSystemAllocationScope);

typedef struct VkAllocationCallbacks {
  void *pUserData;
  PFN_vkAllocationFunction pfnAllocation;
  PFN_vkReallocationFunction pfnReallocation;
  PFN_vkFreeFunction pfnFree;
  PFN_vkInternalAllocationNotification pfnInternalAllocation;
  PFN_vkInternalFreeNotification pfnInternalFree;
} VkAllocationCallbacks;

typedef struct VkApplicationInfo {
  VkStructureType sType;
  const void *pNext;
  const char *pApplicationName;
  uint32_t applicationVersion;
  const char *pEngineName;
  uint32_t engineVersion;
  uint32_t apiVersion;
} VkApplicationInfo;

typedef struct VkInstanceCreateInfo {
  VkStructureType sType;
  const void *pNext;
  VkFlags flags;
  const VkApplicationInfo *pApplicationInfo;
  uint32_t enabledLayerCount;
  const char *const *ppEnabledLayerNames;
  uint32_t enabledExtensionCount;
  const char *const *ppEnabledExtensionNames;
} VkInstanceCreateInfo;

typedef VkResult (*PFN_vkCreateInstance)(const VkInstanceCreateInfo *, const VkAllocationCallbacks *, VkInstance *);
typedef void (*PFN_vkDestroyInstance)(VkInstance, const VkAllocationCallbacks *);

#define VK_SUCCESS 0
#define VK_STRUCTURE_TYPE_APPLICATION_INFO 0u
#define VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO 1u
#define VK_API_VERSION_1_0 (1u << 22)

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

int main(int argc, char **argv) {
  int create_uses_allocator = 1;
  if (argc == 2 && strcmp(argv[1], "--simulate-fex-create-null") == 0) create_uses_allocator = 0;
  else if (argc != 1) {
    fprintf(stderr, "usage: %s [--simulate-fex-create-null]\n", argv[0]);
    return 64;
  }

  void *h = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!h) { fprintf(stderr, "SKIP dlopen: %s\n", dlerror()); return 77; }
  PFN_vkCreateInstance create = (PFN_vkCreateInstance)dlsym(h, "vkCreateInstance");
  PFN_vkDestroyInstance destroy = (PFN_vkDestroyInstance)dlsym(h, "vkDestroyInstance");
  if (!create || !destroy) { fprintf(stderr, "SKIP core symbols\n"); return 77; }

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
    .pApplicationName = "fex-vulkan-allocator-probe",
    .applicationVersion = 1,
    .pEngineName = "none",
    .engineVersion = 1,
    .apiVersion = VK_API_VERSION_1_0,
  };
  VkInstanceCreateInfo ci = {
    .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
    .pApplicationInfo = &app,
  };

  fprintf(stderr, "CASE allocator create_allocator=%s destroy_allocator=yes\n", create_uses_allocator ? "yes" : "no");
  fprintf(stderr, "ALLOC_CALLBACK=%p REALLOC_CALLBACK=%p FREE_CALLBACK=%p\n",
          (void *)allocation_cb, (void *)reallocation_cb, (void *)free_cb);
  fprintf(stderr, "MARK create-enter\n"); fflush(stderr);
  VkInstance instance = NULL;
  VkResult r = create(&ci, create_uses_allocator ? &callbacks : NULL, &instance);
  fprintf(stderr, "MARK create-return result=%d instance=%p alloc=%u realloc=%u free=%u\n",
          r, (void *)instance, alloc_calls, realloc_calls, free_calls); fflush(stderr);
  if (r != VK_SUCCESS) return 2;
  unsigned before_destroy = free_calls;
  fprintf(stderr, "MARK destroy-enter\n"); fflush(stderr);
  destroy(instance, &callbacks);
  fprintf(stderr, "MARK destroy-return alloc=%u realloc=%u free=%u free_delta=%u\n",
          alloc_calls, realloc_calls, free_calls, free_calls - before_destroy); fflush(stderr);

  if (create_uses_allocator) {
    if (alloc_calls == 0 || free_calls == 0) {
      fprintf(stderr, "FAIL allocator callbacks not observed\n");
      return 10;
    }
    fprintf(stderr, "PASS allocator native-valid create/destroy callbacks observed\n");
    return 0;
  }

  fprintf(stderr, "CONTROL create used NULL; destroy used callbacks (Vulkan-invalid mismatch simulation)\n");
  return 0;
}
