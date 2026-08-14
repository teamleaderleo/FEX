#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

int main(void) {
  void *h = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!h) { fprintf(stderr, "DLERROR %s\n", dlerror()); return 2; }
  PFN_vkGetInstanceProcAddr gipa = (PFN_vkGetInstanceProcAddr)dlsym(h, "vkGetInstanceProcAddr");
  if (!gipa) return 3;

  const char *names[] = {
    "vkGetDeviceProcAddr",
    "vkCreateInstance",
    "vkEnumerateInstanceExtensionProperties",
    "vkCreateDevice",
    "vkCreateDebugUtilsMessengerEXT",
  };
  int bad = 0;
  for (unsigned i = 0; i < sizeof(names) / sizeof(names[0]); ++i) {
    PFN_vkVoidFunction p = gipa(VK_NULL_HANDLE, names[i]);
    fprintf(stderr, "NULL_GIPA name=%s ptr=%p\n", names[i], (void *)p);
    if (!strcmp(names[i], "vkGetDeviceProcAddr") && p) bad |= 1;
    if (!strcmp(names[i], "vkCreateInstance") && !p) bad |= 2;
    if (!strcmp(names[i], "vkEnumerateInstanceExtensionProperties") && !p) bad |= 4;
    if (!strcmp(names[i], "vkCreateDevice") && p) bad |= 8;
    if (!strcmp(names[i], "vkCreateDebugUtilsMessengerEXT") && p) bad |= 16;
  }
  fprintf(stderr, "NULL_GIPA_RESULT bad=%d\n", bad);
  return bad ? 20 + bad : 0;
}
