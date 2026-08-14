#define _GNU_SOURCE
#include <xf86drm.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static volatile unsigned load_count;

__attribute__((used,noinline)) static int load_module_body(const char *name) {
  ++load_count;
  fprintf(stderr, "DRM_SERVER_CALLBACK count=%u name=%s\n", load_count, name ? name : "(null)");
  fflush(stderr);
  return 0;
}

#if defined(__x86_64__)
__attribute__((used,naked,noinline)) static int load_module_cb(const char *name) {
  (void)name;
  __asm__ volatile(".byte 0xe9,0,0,0,0\n\tjmp load_module_body");
}
#else
static int load_module_cb(const char *name) { return load_module_body(name); }
#endif

typedef void (*SetServerInfoFn)(drmServerInfoPtr);
typedef int (*DrmOpenFn)(const char *, const char *);
typedef int (*DrmAvailableFn)(void);

struct range { uintptr_t lo, hi; };

static int line_for_addr(uintptr_t addr, char *path, size_t path_size) {
  FILE *f = fopen("/proc/self/maps", "r");
  if (!f) return -1;
  char line[4096];
  while (fgets(line, sizeof(line), f)) {
    unsigned long long lo, hi, off, inode;
    char perms[8], dev[32], mapped[2048] = {0};
    int n = sscanf(line, "%llx-%llx %7s %llx %31s %llu %2047[^\n]", &lo, &hi, perms, &off, dev, &inode, mapped);
    if (n >= 6 && addr >= (uintptr_t)lo && addr < (uintptr_t)hi) {
      if (n == 7) {
        char *p = mapped;
        while (*p == ' ' || *p == '\t') ++p;
        snprintf(path, path_size, "%s", p);
      } else {
        path[0] = 0;
      }
      fclose(f);
      return 0;
    }
  }
  fclose(f);
  return -1;
}

static int collect_path_ranges(const char *path, struct range *out, size_t cap) {
  FILE *f = fopen("/proc/self/maps", "r");
  if (!f) return -1;
  char line[4096];
  size_t count = 0;
  while (fgets(line, sizeof(line), f)) {
    unsigned long long lo, hi, off, inode;
    char perms[8], dev[32], mapped[2048] = {0};
    int n = sscanf(line, "%llx-%llx %7s %llx %31s %llu %2047[^\n]", &lo, &hi, perms, &off, dev, &inode, mapped);
    if (n != 7) continue;
    char *p = mapped;
    while (*p == ' ' || *p == '\t') ++p;
    if (strcmp(p, path) != 0) continue;
    if (count >= cap) { fclose(f); return -2; }
    out[count++] = (struct range){(uintptr_t)lo, (uintptr_t)hi};
  }
  fclose(f);
  return (int)count;
}

static int addr_mapped(uintptr_t addr) {
  char path[2048];
  return line_for_addr(addr, path, sizeof(path)) == 0;
}

static int reserve_ranges(const struct range *ranges, int count) {
  for (int i = 0; i < count; ++i) {
    size_t len = ranges[i].hi - ranges[i].lo;
    void *p = mmap((void *)ranges[i].lo, len, PROT_NONE,
                   MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    if (p == MAP_FAILED) {
      fprintf(stderr, "RESERVE_FAIL %p-%p errno=%d %s\n",
              (void *)ranges[i].lo, (void *)ranges[i].hi, errno, strerror(errno));
      return -1;
    }
    fprintf(stderr, "RESERVED %p-%p\n", (void *)ranges[i].lo, (void *)ranges[i].hi);
  }
  return 0;
}

int main(void) {
  void *lib1 = dlopen("libdrm.so.2", RTLD_NOW | RTLD_LOCAL);
  if (!lib1) { fprintf(stderr, "SKIP dlopen1: %s\n", dlerror()); return 77; }
  SetServerInfoFn set_info1 = (SetServerInfoFn)dlsym(lib1, "drmSetServerInfo");
  DrmOpenFn open1 = (DrmOpenFn)dlsym(lib1, "drmOpen");
  DrmAvailableFn available1 = (DrmAvailableFn)dlsym(lib1, "drmAvailable");
  if (!set_info1 || !open1 || !available1) { fprintf(stderr, "SKIP missing gen1 symbol\n"); return 77; }

  int available = available1();
  if (available) { fprintf(stderr, "SKIP hosted DRM available=%d\n", available); return 77; }

  char wrapper_path[2048];
  if (line_for_addr((uintptr_t)set_info1, wrapper_path, sizeof(wrapper_path)) != 0 || !wrapper_path[0]) {
    fprintf(stderr, "SKIP could not resolve wrapper path\n"); return 77;
  }
  struct range ranges[32];
  int range_count = collect_path_ranges(wrapper_path, ranges, 32);
  if (range_count <= 0) { fprintf(stderr, "SKIP wrapper ranges=%d path=%s\n", range_count, wrapper_path); return 77; }

  drmServerInfo info;
  memset(&info, 0, sizeof(info));
  info.load_module = load_module_cb;

  fprintf(stderr, "GEN1 wrapper=%s set=%p open=%p callback=%p ranges=%d\n",
          wrapper_path, (void *)set_info1, (void *)open1, (void *)load_module_cb, range_count);
  fprintf(stderr, "MARK set-info-enter\n"); fflush(stderr);
  set_info1(&info);
  fprintf(stderr, "MARK set-info-return count=%u\n", load_count); fflush(stderr);
  if (load_count != 0) return 20;

  uintptr_t old_set = (uintptr_t)set_info1;
  fprintf(stderr, "MARK close1-enter\n"); fflush(stderr);
  if (dlclose(lib1) != 0) { fprintf(stderr, "SKIP dlclose1: %s\n", dlerror()); return 77; }
  fprintf(stderr, "MARK close1-return old_set_mapped=%d\n", addr_mapped(old_set)); fflush(stderr);
  if (addr_mapped(old_set)) { fprintf(stderr, "SKIP wrapper did not physically unload\n"); return 77; }

  if (reserve_ranges(ranges, range_count) != 0) return 78;

  void *lib2 = dlopen("libdrm.so.2", RTLD_NOW | RTLD_LOCAL);
  if (!lib2) { fprintf(stderr, "SKIP dlopen2: %s\n", dlerror()); return 77; }
  SetServerInfoFn set_info2 = (SetServerInfoFn)dlsym(lib2, "drmSetServerInfo");
  DrmOpenFn open2 = (DrmOpenFn)dlsym(lib2, "drmOpen");
  if (!set_info2 || !open2) { fprintf(stderr, "SKIP missing gen2 symbol\n"); return 77; }
  fprintf(stderr, "GEN2 set=%p open=%p moved=%d\n", (void *)set_info2, (void *)open2, set_info2 != set_info1);
  fflush(stderr);
  if (set_info2 == set_info1) { fprintf(stderr, "SKIP generation 2 did not move\n"); return 77; }

  /* Critical negative control: do NOT call drmSetServerInfo in generation 2. */
  fprintf(stderr, "MARK open2-enter retained-registration-only\n"); fflush(stderr);
  int fd = open2("fex-intentionally-missing-drm-driver", NULL);
  fprintf(stderr, "MARK open2-return fd=%d callbacks=%u\n", fd, load_count); fflush(stderr);
  if (fd >= 0) return 21;
  if (load_count != 1) return 22;
  return 0;
}
