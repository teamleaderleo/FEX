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

static unsigned char *read_file(const char *path, size_t *size_out) {
  FILE *f = fopen(path, "rb"); if (!f) return NULL;
  if (fseek(f, 0, SEEK_END)) { fclose(f); return NULL; }
  long n = ftell(f); if (n <= 0 || fseek(f, 0, SEEK_SET)) { fclose(f); return NULL; }
  unsigned char *p = malloc((size_t)n); if (!p) { fclose(f); return NULL; }
  if (fread(p, 1, (size_t)n, f) != (size_t)n) { free(p); fclose(f); return NULL; }
  fclose(f); *size_out = (size_t)n; return p;
}

#define LOAD(name) PFN_##name name = (PFN_##name)dlsym(h, #name)

int main(int argc, char **argv) {
  if (argc != 2) return 64;
  void *h = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL); if (!h) return 77;
  LOAD(vkCreateInstance); LOAD(vkDestroyInstance); LOAD(vkEnumeratePhysicalDevices);
  LOAD(vkGetPhysicalDeviceQueueFamilyProperties); LOAD(vkGetPhysicalDeviceMemoryProperties);
  LOAD(vkCreateDevice); LOAD(vkDestroyDevice); LOAD(vkAllocateMemory); LOAD(vkFreeMemory);
  LOAD(vkCreateShaderModule); LOAD(vkDestroyShaderModule);
  if (!vkCreateInstance || !vkDestroyInstance || !vkEnumeratePhysicalDevices || !vkGetPhysicalDeviceQueueFamilyProperties ||
      !vkGetPhysicalDeviceMemoryProperties || !vkCreateDevice || !vkDestroyDevice || !vkAllocateMemory || !vkFreeMemory ||
      !vkCreateShaderModule || !vkDestroyShaderModule) return 77;

  VkAllocationCallbacks cb = {.pUserData=&cookie,.pfnAllocation=allocation_cb,.pfnReallocation=reallocation_cb,.pfnFree=free_cb};
  VkApplicationInfo app={.sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,.pApplicationName="allocator-matrix",.apiVersion=VK_API_VERSION_1_0};
  VkInstanceCreateInfo ici={.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,.pApplicationInfo=&app};
  VkInstance instance=VK_NULL_HANDLE;
  VkResult r=vkCreateInstance(&ici,NULL,&instance); if(r!=VK_SUCCESS)return 2;
  uint32_t np=0; r=vkEnumeratePhysicalDevices(instance,&np,NULL); if(r!=VK_SUCCESS||!np)return 3;
  VkPhysicalDevice *ps=calloc(np,sizeof(*ps)); if(!ps)return 4;
  r=vkEnumeratePhysicalDevices(instance,&np,ps); if(r!=VK_SUCCESS)return 5;
  uint32_t nq=0; vkGetPhysicalDeviceQueueFamilyProperties(ps[0],&nq,NULL); if(!nq)return 6;
  VkQueueFamilyProperties *q=calloc(nq,sizeof(*q)); if(!q)return 7;
  vkGetPhysicalDeviceQueueFamilyProperties(ps[0],&nq,q);
  uint32_t family=0; while(family<nq&&!q[family].queueCount)++family; if(family==nq)return 8;
  float pri=1.0f; VkDeviceQueueCreateInfo qci={.sType=VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,.queueFamilyIndex=family,.queueCount=1,.pQueuePriorities=&pri};
  VkDeviceCreateInfo dci={.sType=VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,.queueCreateInfoCount=1,.pQueueCreateInfos=&qci};

  unsigned a0=alloc_calls,f0=free_calls;
  VkDevice device=VK_NULL_HANDLE;
  fprintf(stderr,"DEVICE_CREATE_ENTER alloc=%u free=%u\n",a0,f0);
  r=vkCreateDevice(ps[0],&dci,&cb,&device);
  fprintf(stderr,"DEVICE_CREATE_RETURN result=%d alloc_delta=%u free_delta=%u\n",r,alloc_calls-a0,free_calls-f0);
  if(r!=VK_SUCCESS)return 9;

  VkPhysicalDeviceMemoryProperties mp; vkGetPhysicalDeviceMemoryProperties(ps[0],&mp); if(!mp.memoryTypeCount)return 10;
  VkMemoryAllocateInfo mai={.sType=VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,.allocationSize=4096,.memoryTypeIndex=0};
  VkDeviceMemory mem=VK_NULL_HANDLE; a0=alloc_calls; f0=free_calls;
  fprintf(stderr,"MEM_ALLOC_ENTER\n"); r=vkAllocateMemory(device,&mai,&cb,&mem);
  fprintf(stderr,"MEM_ALLOC_RETURN result=%d alloc_delta=%u free_delta=%u memory=%p\n",r,alloc_calls-a0,free_calls-f0,(void*)(uintptr_t)mem);
  if(r!=VK_SUCCESS)return 11;
  f0=free_calls; fprintf(stderr,"MEM_FREE_ENTER\n"); vkFreeMemory(device,mem,&cb);
  fprintf(stderr,"MEM_FREE_RETURN free_delta=%u\n",free_calls-f0);

  size_t spv_size=0; unsigned char *spv=read_file(argv[1],&spv_size); if(!spv || (spv_size&3))return 12;
  VkShaderModuleCreateInfo sci={.sType=VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,.codeSize=spv_size,.pCode=(const uint32_t*)spv};
  VkShaderModule shader=VK_NULL_HANDLE; a0=alloc_calls; f0=free_calls;
  fprintf(stderr,"SHADER_CREATE_ENTER\n"); r=vkCreateShaderModule(device,&sci,&cb,&shader);
  fprintf(stderr,"SHADER_CREATE_RETURN result=%d alloc_delta=%u free_delta=%u shader=%p\n",r,alloc_calls-a0,free_calls-f0,(void*)(uintptr_t)shader);
  if(r!=VK_SUCCESS)return 13;
  f0=free_calls; vkDestroyShaderModule(device,shader,&cb);
  fprintf(stderr,"SHADER_DESTROY_RETURN free_delta=%u\n",free_calls-f0); free(spv);

  f0=free_calls; fprintf(stderr,"DEVICE_DESTROY_ENTER\n"); vkDestroyDevice(device,&cb);
  fprintf(stderr,"DEVICE_DESTROY_RETURN free_delta=%u totals=%u/%u/%u\n",free_calls-f0,alloc_calls,realloc_calls,free_calls);
  vkDestroyInstance(instance,NULL); free(q); free(ps);
  if(!alloc_calls || !free_calls)return 14;
  fprintf(stderr,"PASS custom-host allocator forwarding matrix\n");
  return 0;
}
