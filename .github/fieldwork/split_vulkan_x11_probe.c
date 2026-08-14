#define VK_USE_PLATFORM_XLIB_KHR
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void die(const char *what, int code) {
  fprintf(stderr, "FAIL %s\n", what);
  exit(code);
}

static int parse_range(const char *line, uintptr_t *lo, uintptr_t *hi) {
  unsigned long long a = 0, b = 0;
  if (sscanf(line, "%llx-%llx", &a, &b) != 2) return 0;
  *lo = (uintptr_t)a;
  *hi = (uintptr_t)b;
  return 1;
}

static int path_for_address(const void *address, char *out, size_t out_size) {
  FILE *f = fopen("/proc/self/maps", "r");
  if (!f) return 0;
  const uintptr_t needle = (uintptr_t)address;
  char line[4096];
  int found = 0;
  while (fgets(line, sizeof(line), f)) {
    uintptr_t lo = 0, hi = 0;
    if (!parse_range(line, &lo, &hi) || needle < lo || needle >= hi) continue;
    char *path = strchr(line, '/');
    if (!path) break;
    path[strcspn(path, "\n")] = '\0';
    snprintf(out, out_size, "%s", path);
    found = 1;
    break;
  }
  fclose(f);
  return found;
}

static int exact_path_count(const char *path) {
  FILE *f = fopen("/proc/self/maps", "r");
  if (!f) return -1;
  char line[4096];
  int n = 0;
  while (fgets(line, sizeof(line), f)) {
    char *mapped = strchr(line, '/');
    if (!mapped) continue;
    mapped[strcspn(mapped, "\n")] = '\0';
    if (strcmp(mapped, path) == 0) ++n;
  }
  fclose(f);
  return n;
}

static int substring_map_count(const char *needle) {
  FILE *f = fopen("/proc/self/maps", "r");
  if (!f) return -1;
  char line[4096];
  int n = 0;
  while (fgets(line, sizeof(line), f)) if (strstr(line, needle)) ++n;
  fclose(f);
  return n;
}

int main(void) {
  setvbuf(stderr, NULL, _IONBF, 0);
  void *vulkan = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!vulkan) { fprintf(stderr, "DLERROR %s\n", dlerror()); return 2; }

  PFN_vkGetInstanceProcAddr gipa = (PFN_vkGetInstanceProcAddr)dlsym(vulkan, "vkGetInstanceProcAddr");
  if (!gipa) die("gipa", 3);

  char wrapper_path[4096] = {};
  if (!path_for_address((const void *)gipa, wrapper_path, sizeof(wrapper_path))) die("wrapper path", 4);
  const int wrapper_before = exact_path_count(wrapper_path);
  const int bridge_before = substring_map_count("libfex-vulkan-bridge.so");
  fprintf(stderr, "WRAPPER_PATH %s\n", wrapper_path);
  fprintf(stderr, "MAPS_BEFORE exact_wrapper=%d bridge=%d\n", wrapper_before, bridge_before);
  if (wrapper_before <= 0 || bridge_before <= 0) die("initial mappings", 5);

  PFN_vkCreateInstance create_instance = (PFN_vkCreateInstance)gipa(VK_NULL_HANDLE, "vkCreateInstance");
  if (!create_instance) die("create_instance", 6);
  const char *exts[] = { VK_KHR_SURFACE_EXTENSION_NAME, VK_KHR_XLIB_SURFACE_EXTENSION_NAME };
  VkApplicationInfo app = { .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "fex-split-x11", .apiVersion = VK_API_VERSION_1_0 };
  VkInstanceCreateInfo ci = { .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app, .enabledExtensionCount = 2, .ppEnabledExtensionNames = exts };
  VkInstance instance = VK_NULL_HANDLE;
  VkResult cr = create_instance(&ci, NULL, &instance);
  fprintf(stderr, "CREATE_INSTANCE result=%d instance=%p\n", cr, (void *)instance);
  if (cr != VK_SUCCESS) return 7;

  PFN_vkEnumeratePhysicalDevices enumerate = (PFN_vkEnumeratePhysicalDevices)gipa(instance, "vkEnumeratePhysicalDevices");
  PFN_vkGetPhysicalDeviceXlibPresentationSupportKHR xlib_support =
    (PFN_vkGetPhysicalDeviceXlibPresentationSupportKHR)gipa(instance, "vkGetPhysicalDeviceXlibPresentationSupportKHR");
  if (!enumerate || !xlib_support) die("required PFN", 8);
  uint32_t count = 0;
  if (enumerate(instance, &count, NULL) != VK_SUCCESS || count == 0) die("enumerate count", 9);
  VkPhysicalDevice *phys = calloc(count, sizeof(*phys));
  if (!phys) return 10;
  if (enumerate(instance, &count, phys) != VK_SUCCESS || count == 0) die("enumerate devices", 11);
  fprintf(stderr, "PHYSICAL count=%u first=%p xlib_pfn=%p\n", count, (void *)phys[0], (void *)xlib_support);

  Display *guest_display_1 = (Display *)(uintptr_t)0x12345000;
  Display *guest_display_2 = (Display *)(uintptr_t)0x12346000;
  VkBool32 before = xlib_support(phys[0], 0, guest_display_1, 0);
  fprintf(stderr, "BEFORE_CLOSE_XLIB result=%u\n", before);

  if (dlclose(vulkan) != 0) die("dlclose", 12);
  const int wrapper_after = exact_path_count(wrapper_path);
  const int bridge_after = substring_map_count("libfex-vulkan-bridge.so");
  fprintf(stderr, "MAPS_AFTER exact_wrapper=%d bridge=%d\n", wrapper_after, bridge_after);
  if (wrapper_after != 0) die("guest wrapper still mapped", 13);
  if (bridge_after <= 0) die("bridge disappeared", 14);

  fprintf(stderr, "AFTER_DLCLOSE_BEGIN_CALLBACK_TEST\n");
  VkBool32 after = xlib_support(phys[0], 0, guest_display_2, 0);
  fprintf(stderr, "AFTER_CLOSE_XLIB result=%u\n", after);
  fprintf(stderr, "REAL_SPLIT_VULKAN_X11_CALLBACK_OK\n");
  free(phys);
  return 0;
}
