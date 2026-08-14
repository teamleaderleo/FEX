#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

struct Header { void *base; size_t size; uint64_t magic; };
#define HEADER_MAGIC UINT64_C(0x46584558414c4c4f)
static volatile unsigned alloc_calls;
static volatile unsigned realloc_calls;
static volatile unsigned free_calls;
static int cookie;
static void *last_allocated;
static void *last_base;

static void trace_u64(const char *tag, uintptr_t a, uintptr_t b, uintptr_t c) {
  char buf[256];
  int n = snprintf(buf, sizeof(buf), "%s a=0x%lx b=0x%lx c=0x%lx\n", tag,
                   (unsigned long)a, (unsigned long)b, (unsigned long)c);
  if (n > 0) write(2, buf, (size_t)n < sizeof(buf) ? (size_t)n : sizeof(buf));
}

static size_t normalize_alignment(size_t a) {
  if (a < sizeof(void *)) a = sizeof(void *);
  size_t p = sizeof(void *);
  while (p < a && p <= SIZE_MAX / 2) p <<= 1;
  return p;
}

__attribute__((used,noinline)) static void *alloc_body(void *user, size_t size, size_t alignment, VkSystemAllocationScope scope) {
  trace_u64("CB_ALLOC_ENTER", (uintptr_t)user, size, alignment);
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
  h->magic = HEADER_MAGIC;
  last_allocated = (void *)aligned;
  last_base = base;
  trace_u64("CB_ALLOC_RETURN", aligned, (uintptr_t)base, (uintptr_t)scope);
  return (void *)aligned;
}

__attribute__((used,noinline)) static void *realloc_body(void *user, void *original, size_t size, size_t alignment, VkSystemAllocationScope scope) {
  trace_u64("CB_REALLOC_ENTER", (uintptr_t)original, size, alignment);
  if (user != &cookie) _exit(92);
  ++realloc_calls;
  if (!original) return alloc_body(user, size, alignment, scope);
  struct Header *oldh = (struct Header *)((uintptr_t)original - sizeof(struct Header));
  trace_u64("CB_REALLOC_HEADER", (uintptr_t)oldh->base, oldh->size, oldh->magic);
  if (oldh->magic != HEADER_MAGIC) _exit(94);
  size_t old_size = oldh->size;
  if (size == 0) {
    void *oldbase = oldh->base;
    oldh->magic = 0;
    free(oldbase);
    trace_u64("CB_REALLOC_FREE_RETURN", (uintptr_t)original, (uintptr_t)oldbase, 0);
    return NULL;
  }
  void *p = alloc_body(user, size, alignment, scope);
  if (!p) return NULL;
  memcpy(p, original, old_size < size ? old_size : size);
  void *oldbase = oldh->base;
  oldh->magic = 0;
  free(oldbase);
  trace_u64("CB_REALLOC_RETURN", (uintptr_t)p, (uintptr_t)oldbase, (uintptr_t)scope);
  return p;
}

__attribute__((used,noinline)) static void free_body(void *user, void *memory) {
  trace_u64("CB_FREE_ENTER", (uintptr_t)user, (uintptr_t)memory, (uintptr_t)last_allocated);
  if (user != &cookie) _exit(93);
  ++free_calls;
  if (!memory) {
    trace_u64("CB_FREE_NULL_RETURN", 0, 0, 0);
    return;
  }
  struct Header *h = (struct Header *)((uintptr_t)memory - sizeof(struct Header));
  trace_u64("CB_FREE_HEADER", (uintptr_t)h->base, h->size, h->magic);
  if (h->magic != HEADER_MAGIC) _exit(95);
  void *base = h->base;
  h->magic = 0;
  free(base);
  trace_u64("CB_FREE_RETURN", (uintptr_t)memory, (uintptr_t)base, (uintptr_t)last_base);
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
  if (!h) return 77;
  LOAD(vkCreateInstance); LOAD(vkDestroyInstance); LOAD(vkEnumeratePhysicalDevices);
  LOAD(vkGetPhysicalDeviceQueueFamilyProperties); LOAD(vkCreateDevice); LOAD(vkDestroyDevice);
  LOAD(vkCreateBuffer); LOAD(vkDestroyBuffer);
  if (!vkCreateInstance || !vkDestroyInstance || !vkEnumeratePhysicalDevices || !vkGetPhysicalDeviceQueueFamilyProperties ||
      !vkCreateDevice || !vkDestroyDevice || !vkCreateBuffer || !vkDestroyBuffer) return 77;

  VkApplicationInfo app = {.sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,.pApplicationName="crosscall-trace",.apiVersion=VK_API_VERSION_1_0};
  VkInstanceCreateInfo ici = {.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,.pApplicationInfo=&app};
  VkInstance instance = VK_NULL_HANDLE;
  VkResult r = vkCreateInstance(&ici, NULL, &instance); if (r != VK_SUCCESS) return 2;
  uint32_t nphys=0; r=vkEnumeratePhysicalDevices(instance,&nphys,NULL); if(r!=VK_SUCCESS||!nphys)return 3;
  VkPhysicalDevice *phys=calloc(nphys,sizeof(*phys)); if(!phys)return 4;
  r=vkEnumeratePhysicalDevices(instance,&nphys,phys); if(r!=VK_SUCCESS)return 5;
  uint32_t nq=0; vkGetPhysicalDeviceQueueFamilyProperties(phys[0],&nq,NULL); if(!nq)return 6;
  VkQueueFamilyProperties *qp=calloc(nq,sizeof(*qp)); if(!qp)return 7;
  vkGetPhysicalDeviceQueueFamilyProperties(phys[0],&nq,qp); uint32_t family=0; while(family<nq&&!qp[family].queueCount)++family; if(family==nq)return 8;
  float pri=1.0f; VkDeviceQueueCreateInfo qci={.sType=VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,.queueFamilyIndex=family,.queueCount=1,.pQueuePriorities=&pri};
  VkDeviceCreateInfo dci={.sType=VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,.queueCreateInfoCount=1,.pQueueCreateInfos=&qci};
  VkDevice device=VK_NULL_HANDLE; r=vkCreateDevice(phys[0],&dci,NULL,&device); if(r!=VK_SUCCESS)return 9;

  VkAllocationCallbacks cb={.pUserData=&cookie,.pfnAllocation=allocation_cb,.pfnReallocation=reallocation_cb,.pfnFree=free_cb};
  VkBufferCreateInfo bci={.sType=VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,.size=4096,.usage=VK_BUFFER_USAGE_TRANSFER_SRC_BIT,.sharingMode=VK_SHARING_MODE_EXCLUSIVE};
  VkBuffer buffer=VK_NULL_HANDLE;
  trace_u64("API_CREATE_ENTER",(uintptr_t)cb.pfnAllocation,(uintptr_t)cb.pfnFree,(uintptr_t)&cookie);
  r=vkCreateBuffer(device,&bci,&cb,&buffer);
  trace_u64("API_CREATE_RETURN",(uintptr_t)r,(uintptr_t)buffer,(uintptr_t)last_allocated);
  if(r!=VK_SUCCESS)return 10;
  trace_u64("API_DESTROY_ENTER",(uintptr_t)buffer,(uintptr_t)last_allocated,(uintptr_t)last_base);
  vkDestroyBuffer(device,buffer,&cb);
  trace_u64("API_DESTROY_RETURN",alloc_calls,realloc_calls,free_calls);

  vkDestroyDevice(device,NULL); vkDestroyInstance(instance,NULL); free(qp); free(phys);
  return (alloc_calls && free_calls) ? 0 : 11;
}
