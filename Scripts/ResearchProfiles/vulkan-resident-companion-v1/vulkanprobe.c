#define _GNU_SOURCE
#define VK_USE_64_BIT_PTR_DEFINES 0
#define VK_USE_PLATFORM_XLIB_KHR
#include <vulkan/vulkan.h>

#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>

struct span {
  uintptr_t begin;
  uintptr_t end;
};

struct map_memory {
  long rss_kib;
  long pss_kib;
  unsigned mappings;
};

static struct span old_wrapper[16];
static unsigned old_wrapper_count;

static uint64_t now_ns(void) {
  struct timespec value;
  if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) exit(60);
  return (uint64_t)value.tv_sec * 1000000000ULL + (uint64_t)value.tv_nsec;
}

static int map_has(void* pointer, const char* needle, int verbose) {
  FILE* file = fopen("/proc/self/maps", "r");
  char line[1024];
  const uintptr_t address = (uintptr_t)pointer;
  if (!file) return -1;
  while (fgets(line, sizeof(line), file)) {
    unsigned long begin;
    unsigned long end;
    if (sscanf(line, "%lx-%lx", &begin, &end) == 2 && address >= begin && address < end) {
      if (verbose) fprintf(stderr, "MAP %p %s", pointer, line);
      const int result = strstr(line, needle) != NULL;
      fclose(file);
      return result;
    }
  }
  fclose(file);
  if (verbose) fprintf(stderr, "UNMAPPED %p\n", pointer);
  return 0;
}

static int maps_contain(const char* needle) {
  FILE* file = fopen("/proc/self/maps", "r");
  char line[1024];
  if (!file) return 0;
  while (fgets(line, sizeof(line), file)) {
    if (strstr(line, needle)) {
      fclose(file);
      return 1;
    }
  }
  fclose(file);
  return 0;
}

static struct map_memory map_memory_for(const char* needle) {
  FILE* file = fopen("/proc/self/smaps", "r");
  char line[1024];
  int selected = 0;
  struct map_memory result = {0};
  if (!file) exit(61);
  while (fgets(line, sizeof(line), file)) {
    unsigned long begin;
    unsigned long end;
    if (sscanf(line, "%lx-%lx", &begin, &end) == 2) {
      selected = strstr(line, needle) != NULL;
      if (selected) ++result.mappings;
      continue;
    }
    if (selected) {
      long value;
      if (sscanf(line, "Rss: %ld kB", &value) == 1) result.rss_kib += value;
      if (sscanf(line, "Pss: %ld kB", &value) == 1) result.pss_kib += value;
    }
  }
  fclose(file);
  return result;
}

static void capture_wrapper(void) {
  FILE* file = fopen("/proc/self/maps", "r");
  char line[1024];
  old_wrapper_count = 0;
  if (!file) exit(70);
  while (fgets(line, sizeof(line), file)) {
    unsigned long begin;
    unsigned long end;
    if (strstr(line, "/usr/lib/x86_64-linux-gnu/libvulkan.so.1") &&
        sscanf(line, "%lx-%lx", &begin, &end) == 2 && old_wrapper_count < 16) {
      old_wrapper[old_wrapper_count++] = (struct span){begin, end};
      fprintf(stderr, "OLD_VULKAN_RANGE %lx-%lx\n", begin, end);
    }
  }
  fclose(file);
  if (!old_wrapper_count) exit(71);
}

static void reserve_old_wrapper(void) {
  for (unsigned index = 0; index < old_wrapper_count; ++index) {
    const size_t size = old_wrapper[index].end - old_wrapper[index].begin;
    void* result = mmap((void*)old_wrapper[index].begin, size, PROT_NONE,
                        MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    if (result == MAP_FAILED) {
      fprintf(stderr, "RESERVE_FAIL %lx-%lx errno=%d %s\n", old_wrapper[index].begin,
              old_wrapper[index].end, errno, strerror(errno));
      exit(72);
    }
  }
}

int main(void) {
  const uint64_t load_start = now_ns();
  void* wrapper = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  const uint64_t load_end = now_ns();
  if (!wrapper) {
    fprintf(stderr, "DLOPEN_FAIL %s\n", dlerror());
    return 2;
  }

  PFN_vkGetInstanceProcAddr get0 = (PFN_vkGetInstanceProcAddr)dlsym(wrapper, "vkGetInstanceProcAddr");
  if (!get0) return 3;
  PFN_vkEnumerateInstanceVersion version0 =
    (PFN_vkEnumerateInstanceVersion)get0(VK_NULL_HANDLE, "vkEnumerateInstanceVersion");
  PFN_vkGetPhysicalDeviceXlibPresentationSupportKHR present0 =
    (PFN_vkGetPhysicalDeviceXlibPresentationSupportKHR)get0((VkInstance)(uintptr_t)0x1000,
                                                            "vkGetPhysicalDeviceXlibPresentationSupportKHR");
  if (!version0 || !present0) return 4;

  uint32_t version = 0;
  if (version0(&version) != VK_SUCCESS || version != VK_MAKE_API_VERSION(0, 1, 3, 151)) return 5;
  fprintf(stderr, "GEN1 get=%p version=%p present=%p value=%u bridge=%d x11=%d load_ns=%llu\n",
          (void*)get0, (void*)version0, (void*)present0, version,
          maps_contain("/libfex-vulkan-bridge.so"), maps_contain("/libX11.so.6"),
          (unsigned long long)(load_end - load_start));
  capture_wrapper();

  if (dlclose(wrapper)) return 6;
  if (map_has((void*)get0, "/usr/lib/x86_64-linux-gnu/libvulkan.so.1", 1) != 0) return 7;
  if (!maps_contain("/libfex-vulkan-bridge.so")) return 8;
  if (!maps_contain("/libX11.so.6")) return 9;
  if (maps_contain("/usr/lib/x86_64-linux-gnu/libvulkan.so.1")) return 18;
  const struct map_memory bridge_memory = map_memory_for("/libfex-vulkan-bridge.so");
  fprintf(stderr, "BRIDGE_AFTER_CLOSE mappings=%u rss_kib=%ld pss_kib=%ld\n",
          bridge_memory.mappings, bridge_memory.rss_kib, bridge_memory.pss_kib);
  reserve_old_wrapper();

  version = 0;
  if (version0(&version) != VK_SUCCESS || version != VK_MAKE_API_VERSION(0, 1, 3, 151)) return 10;
  void* display = malloc(16);
  if (!display) return 11;
  fprintf(stderr, "POST_CLOSE_XLIB_BEGIN H=%p display=%p\n", (void*)present0, display);
  const VkBool32 supported = present0((VkPhysicalDevice)(uintptr_t)0x2000, 7, (Display*)display, 42);
  fprintf(stderr, "POST_CLOSE_XLIB_END supported=%u\n", supported);
  if (supported != VK_TRUE) return 12;
  fprintf(stderr, "RESIDENT_VULKAN_XLIB_CALLBACK_AFTER_CLOSE_OK\n");
  free(display);

  void* wrapper2 = dlopen("libvulkan.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!wrapper2) {
    fprintf(stderr, "RELOAD_FAIL %s\n", dlerror());
    return 13;
  }
  PFN_vkGetInstanceProcAddr get1 = (PFN_vkGetInstanceProcAddr)dlsym(wrapper2, "vkGetInstanceProcAddr");
  PFN_vkEnumerateInstanceVersion version1 =
    (PFN_vkEnumerateInstanceVersion)get1(VK_NULL_HANDLE, "vkEnumerateInstanceVersion");
  fprintf(stderr, "GEN2 get_old=%p get_new=%p moved=%d H_old=%p H_new=%p same_H=%d bridge=%d\n",
          (void*)get0, (void*)get1, get0 != get1, (void*)version0, (void*)version1,
          version0 == version1, maps_contain("/libfex-vulkan-bridge.so"));
  if (get0 == get1 || version0 != version1) return 14;
  version = 0;
  if (!version1 || version1(&version) != VK_SUCCESS || version != VK_MAKE_API_VERSION(0, 1, 3, 151)) return 15;
  if (dlclose(wrapper2)) return 16;
  version = 0;
  if (version0(&version) != VK_SUCCESS || version != VK_MAKE_API_VERSION(0, 1, 3, 151)) return 17;
  fprintf(stderr, "VULKAN_RESIDENT_COMPANION_RUNTIME_OK\n");
  return 0;
}
