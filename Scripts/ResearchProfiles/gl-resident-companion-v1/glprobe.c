#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>

typedef void (*VoidFunc)(void);
typedef VoidFunc (*GLXGET)(const unsigned char*);
typedef unsigned int (*GLGETERROR)(void);
typedef int (*GLXQUERYEXT)(void*, int*, int*);

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
      const int result = strstr(line, needle) != 0;
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
      selected = strstr(line, needle) != 0;
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
    if (strstr(line, "/usr/lib/x86_64-linux-gnu/libGL.so.1") &&
        sscanf(line, "%lx-%lx", &begin, &end) == 2 && old_wrapper_count < 16) {
      old_wrapper[old_wrapper_count++] = (struct span){begin, end};
      fprintf(stderr, "OLD_GL_RANGE %lx-%lx\n", begin, end);
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
  void* wrapper = dlopen("libGL.so.1", RTLD_NOW | RTLD_LOCAL);
  const uint64_t load_end = now_ns();
  if (!wrapper) {
    fprintf(stderr, "DLOPEN_FAIL %s\n", dlerror());
    return 2;
  }
  GLXGET get0 = (GLXGET)dlsym(wrapper, "glXGetProcAddress");
  if (!get0) return 3;
  GLGETERROR error0 = (GLGETERROR)get0((const unsigned char*)"glGetError");
  if (!error0) return 4;
  GLXQUERYEXT query0 = (GLXQUERYEXT)get0((const unsigned char*)"glXQueryExtension");
  if (!query0) return 16;
  const unsigned int first_error = error0();
  fprintf(stderr, "GEN1 get=%p H=%p glxH=%p error=%u bridge=%d x11=%d load_ns=%llu\n",
          (void*)get0, (void*)error0, (void*)query0, first_error,
          maps_contain("/libfex-GL-bridge.so"), maps_contain("/libX11.so.6"),
          (unsigned long long)(load_end - load_start));
  if (first_error != 0) return 5;
  if (!maps_contain("/libfex-GL-bridge.so")) return 6;
  capture_wrapper();

  if (dlclose(wrapper)) return 7;
  if (map_has((void*)get0, "/usr/lib/x86_64-linux-gnu/libGL.so.1", 1) != 0) return 8;
  if (!maps_contain("/libfex-GL-bridge.so")) return 9;
  if (!maps_contain("/libX11.so.6")) return 19;
  const struct map_memory bridge_memory = map_memory_for("/libfex-GL-bridge.so");
  fprintf(stderr, "BRIDGE_AFTER_CLOSE mappings=%u rss_kib=%ld pss_kib=%ld\n",
          bridge_memory.mappings, bridge_memory.rss_kib, bridge_memory.pss_kib);

  const unsigned int retained_error = error0();
  fprintf(stderr, "RETAINED_AFTER_CLOSE error=%u\n", retained_error);
  if (retained_error != 0) return 10;
  void* display = malloc(16);
  if (!display) return 17;
  int error_base = -1;
  int event_base = -1;
  fprintf(stderr, "POST_CLOSE_GLX_BEGIN H=%p display=%p\n", (void*)query0, display);
  const int query_result = query0(display, &error_base, &event_base);
  fprintf(stderr, "POST_CLOSE_GLX_END rc=%d error=%d event=%d\n", query_result, error_base, event_base);
  fprintf(stderr, "RESIDENT_GLX_CALLBACK_AFTER_CLOSE_OK\n");
  free(display);

  reserve_old_wrapper();
  void* wrapper2 = dlopen("libGL.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!wrapper2) {
    fprintf(stderr, "RELOAD_FAIL %s\n", dlerror());
    return 11;
  }
  GLXGET get1 = (GLXGET)dlsym(wrapper2, "glXGetProcAddress");
  GLGETERROR error1 = (GLGETERROR)get1((const unsigned char*)"glGetError");
  fprintf(stderr, "GEN2 get_old=%p get_new=%p moved=%d H_old=%p H_new=%p same_H=%d bridge=%d\n",
          (void*)get0, (void*)get1, get0 != get1, (void*)error0, (void*)error1,
          error0 == error1, maps_contain("/libfex-GL-bridge.so"));
  if (get0 == get1 || error0 != error1) return 12;
  if (error1() != 0) return 13;
  if (dlclose(wrapper2)) return 14;
  if (error0() != 0) return 15;
  fprintf(stderr, "GL_RESIDENT_COMPANION_RUNTIME_OK\n");
  return 0;
}
