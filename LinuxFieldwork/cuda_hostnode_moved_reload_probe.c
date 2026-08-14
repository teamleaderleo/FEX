#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

typedef int CUresult;
typedef void *CUgraph;
typedef void *CUgraphNode;
typedef void *CUgraphExec;
typedef void *CUstream;
typedef void (*CUhostFn)(void *userData);
typedef struct CUDA_HOST_NODE_PARAMS_st { CUhostFn fn; void *userData; } CUDA_HOST_NODE_PARAMS;
typedef CUresult (*AddHostFn)(CUgraphNode *, CUgraph, const CUgraphNode *, size_t, const CUDA_HOST_NODE_PARAMS *);
typedef CUresult (*GraphLaunchFn)(CUgraphExec, CUstream);

static volatile unsigned callback_count;
__attribute__((used,noinline)) static void callback_body(void *user) {
  ++callback_count;
  fprintf(stderr, "CUDA_RETAINED_CALLBACK count=%u user=%p\n", callback_count, user);
  fflush(stderr);
}
#if defined(__x86_64__)
__attribute__((used,naked,noinline)) static void callback_entry(void *user) {
  (void)user;
  __asm__ volatile(".byte 0xe9,0,0,0,0\n\tjmp callback_body");
}
#else
static void callback_entry(void *user) { callback_body(user); }
#endif

struct range { uintptr_t lo, hi; };
static int line_for_addr(uintptr_t addr, char *path, size_t path_size) {
  FILE *f = fopen("/proc/self/maps", "r"); if (!f) return -1;
  char line[4096];
  while (fgets(line, sizeof(line), f)) {
    unsigned long long lo, hi, off, inode; char perms[8], dev[32], mapped[2048] = {0};
    int n = sscanf(line, "%llx-%llx %7s %llx %31s %llu %2047[^\n]", &lo, &hi, perms, &off, dev, &inode, mapped);
    if (n >= 6 && addr >= (uintptr_t)lo && addr < (uintptr_t)hi) {
      if (n == 7) { char *p = mapped; while (*p == ' ' || *p == '\t') ++p; snprintf(path, path_size, "%s", p); }
      else path[0] = 0;
      fclose(f); return 0;
    }
  }
  fclose(f); return -1;
}
static int collect_path_ranges(const char *path, struct range *out, size_t cap) {
  FILE *f = fopen("/proc/self/maps", "r"); if (!f) return -1;
  char line[4096]; size_t count = 0;
  while (fgets(line, sizeof(line), f)) {
    unsigned long long lo, hi, off, inode; char perms[8], dev[32], mapped[2048] = {0};
    int n = sscanf(line, "%llx-%llx %7s %llx %31s %llu %2047[^\n]", &lo, &hi, perms, &off, dev, &inode, mapped);
    if (n != 7) continue; char *p = mapped; while (*p == ' ' || *p == '\t') ++p;
    if (strcmp(p, path) != 0) continue; if (count >= cap) { fclose(f); return -2; }
    out[count++] = (struct range){(uintptr_t)lo, (uintptr_t)hi};
  }
  fclose(f); return (int)count;
}
static int addr_mapped(uintptr_t addr) { char p[2048]; return line_for_addr(addr, p, sizeof(p)) == 0; }
static int reserve_ranges(const struct range *ranges, int count) {
  for (int i = 0; i < count; ++i) {
    size_t len = ranges[i].hi - ranges[i].lo;
    void *p = mmap((void *)ranges[i].lo, len, PROT_NONE, MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE, -1, 0);
    if (p == MAP_FAILED) { fprintf(stderr, "RESERVE_FAIL %p-%p errno=%d %s\n", (void*)ranges[i].lo, (void*)ranges[i].hi, errno, strerror(errno)); return -1; }
    fprintf(stderr, "RESERVED %p-%p\n", (void*)ranges[i].lo, (void*)ranges[i].hi);
  }
  return 0;
}

int main(void) {
  void *lib1 = dlopen("libcuda.so.1", RTLD_NOW|RTLD_LOCAL);
  if (!lib1) { fprintf(stderr, "SKIP dlopen1: %s\n", dlerror()); return 77; }
  AddHostFn add1 = (AddHostFn)dlsym(lib1, "cuGraphAddHostNode");
  GraphLaunchFn launch1 = (GraphLaunchFn)dlsym(lib1, "cuGraphLaunch");
  if (!add1 || !launch1) { fprintf(stderr, "SKIP missing gen1 symbols\n"); return 77; }

  char wrapper_path[2048];
  if (line_for_addr((uintptr_t)add1, wrapper_path, sizeof(wrapper_path)) != 0 || !wrapper_path[0]) return 77;
  struct range ranges[32]; int range_count = collect_path_ranges(wrapper_path, ranges, 32);
  if (range_count <= 0) { fprintf(stderr, "SKIP ranges=%d path=%s\n", range_count, wrapper_path); return 77; }

  CUDA_HOST_NODE_PARAMS params = { .fn = callback_entry, .userData = (void*)(uintptr_t)0x12345678 };
  CUgraphNode node = NULL;
  fprintf(stderr, "GEN1 wrapper=%s add=%p launch=%p callback=%p ranges=%d\n", wrapper_path, (void*)add1, (void*)launch1, (void*)callback_entry, range_count);
  fprintf(stderr, "MARK add1-enter\n"); fflush(stderr);
  CUresult rc = add1(&node, (CUgraph)(uintptr_t)0x1111, NULL, 0, &params);
  fprintf(stderr, "MARK add1-return rc=%d node=%p callbacks=%u\n", rc, node, callback_count); fflush(stderr);
  if (rc != 0 || callback_count != 0) return 20;

  uintptr_t old_add = (uintptr_t)add1;
  fprintf(stderr, "MARK close1-enter\n"); fflush(stderr);
  if (dlclose(lib1) != 0) return 77;
  fprintf(stderr, "MARK close1-return old_add_mapped=%d\n", addr_mapped(old_add)); fflush(stderr);
  if (addr_mapped(old_add)) { fprintf(stderr, "SKIP wrapper did not physically unload\n"); return 77; }
  if (reserve_ranges(ranges, range_count) != 0) return 78;

  void *lib2 = dlopen("libcuda.so.1", RTLD_NOW|RTLD_LOCAL);
  if (!lib2) { fprintf(stderr, "SKIP dlopen2: %s\n", dlerror()); return 77; }
  AddHostFn add2 = (AddHostFn)dlsym(lib2, "cuGraphAddHostNode");
  GraphLaunchFn launch2 = (GraphLaunchFn)dlsym(lib2, "cuGraphLaunch");
  if (!add2 || !launch2) return 77;
  fprintf(stderr, "GEN2 add=%p launch=%p moved=%d\n", (void*)add2, (void*)launch2, add2 != add1); fflush(stderr);
  if (add2 == add1) { fprintf(stderr, "SKIP generation 2 did not move\n"); return 77; }

  /* Critical control: generation 2 does not call cuGraphAddHostNode again. */
  fprintf(stderr, "MARK launch2-enter retained-registration-only\n"); fflush(stderr);
  rc = launch2((CUgraphExec)(uintptr_t)0x2222, (CUstream)(uintptr_t)0x3333);
  fprintf(stderr, "MARK launch2-return rc=%d callbacks=%u\n", rc, callback_count); fflush(stderr);
  if (rc != 0 || callback_count != 1) return 22;
  return 0;
}
