#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

static unsigned alloc_calls;
static unsigned free_calls;
static int cookie;

static void *VKAPI_CALL allocation_cb(void *user, size_t size, size_t alignment, VkSystemAllocationScope scope) {
  (void)scope;
  if (user != &cookie) return NULL;
  ++alloc_calls;
  if (alignment < sizeof(void *)) alignment = sizeof(void *);
  void *p = NULL;
  if (posix_memalign(&p, alignment, size ? size : 1) != 0) return NULL;
  fprintf(stderr, "NOFREE_ALLOC ptr=%p size=%zu alignment=%zu\n", p, size, alignment);
  return p;
}

static void *VKAPI_CALL reallocation_cb(void *user, void *original, size_t size, size_t alignment, VkSystemAllocationScope scope) {
  (void)user; (void)original; (void)size; (void)alignment; (void)scope;
  fprintf(stderr, "NOFREE_REALLOC_UNEXPECTED\n");
  return NULL;
}

static void VKAPI_CALL free_cb(void *user, void *memory) {
  if (user != &cookie) abort();
  ++free_calls;
  fprintf(stderr, "NOFREE_FREE_ENTER ptr=%p count=%u\n", memory, free_calls);
  /* Deliberately leak. This is a discriminator for callback transition versus guest libc free. */
  fprintf(stderr, "NOFREE_FREE_RETURN ptr=%p\n", memory);
}

#define LOAD(name) PFN_##name name = (PFN_##name)dlsym(h, #name)

int main(void) {
  void *h = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!h) return 77;
  LOAD(vkCreateInstance); LOAD(vkDestroyInstance); LOAD(vkEnumeratePhysicalDevices);
  LOAD(vkGetPhysicalDeviceQueueFamilyProperties); LOAD(vkCreateDevice); LOAD(vkDestroyDevice);
  LOAD(vkCreateBuffer); LOAD(vkDestroyBuffer);
  if (!vkCreateInstance || !vkDestroyInstance || !vkEnumeratePhysicalDevices || !vkGetPhysicalDeviceQueueFamilyProperties ||
      !vkCreateDevice || !vkDestroyDevice || !vkCreateBuffer || !vkDestroyBuffer) return 77;

  VkApplicationInfo app = {.sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,.pApplicationName="allocator-nofree",.apiVersion=VK_API_VERSION_1_0};
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
  fprintf(stderr, "NOFREE_CREATE_ENTER\n");
  r=vkCreateBuffer(device,&bci,&cb,&buffer);
  fprintf(stderr, "NOFREE_CREATE_RETURN result=%d buffer=%p alloc=%u free=%u\n", r, (void*)(uintptr_t)buffer, alloc_calls, free_calls);
  if(r!=VK_SUCCESS)return 10;
  fprintf(stderr, "NOFREE_DESTROY_ENTER\n");
  vkDestroyBuffer(device,buffer,&cb);
  fprintf(stderr, "NOFREE_DESTROY_RETURN alloc=%u free=%u\n",alloc_calls,free_calls);

  vkDestroyDevice(device,NULL); vkDestroyInstance(instance,NULL); free(qp); free(phys);
  if (!alloc_calls || !free_calls) return 11;
  fprintf(stderr, "PASS no-free callback returned across destroy\n");
  return 0;
}
