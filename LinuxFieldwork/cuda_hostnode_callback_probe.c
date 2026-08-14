#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef int CUresult;
typedef void *CUgraph;
typedef void *CUgraphNode;
typedef void (*CUhostFn)(void *);
typedef struct CUDA_HOST_NODE_PARAMS_st {
  CUhostFn fn;
  void *userData;
} CUDA_HOST_NODE_PARAMS;

typedef CUresult (*cuGraphAddHostNodeFn)(CUgraphNode *, CUgraph, const CUgraphNode *, size_t, const CUDA_HOST_NODE_PARAMS *);

static volatile unsigned callback_count;

__attribute__((used,noinline)) static void host_body(void *data) {
  ++callback_count;
  fprintf(stderr, "CUDA_HOST_CALLBACK count=%u data=%p\n", callback_count, data);
  fflush(stderr);
}

#if defined(__x86_64__)
__attribute__((used,naked,noinline)) static void host_cb(void *data) {
  (void)data;
  __asm__ volatile(".byte 0xe9,0,0,0,0\n\tjmp host_body");
}
#else
static void host_cb(void *data) { host_body(data); }
#endif

int main(void) {
  void *lib = dlopen("libcuda.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!lib) {
    fprintf(stderr, "SKIP dlopen libcuda.so.1: %s\n", dlerror());
    return 77;
  }
  cuGraphAddHostNodeFn add = (cuGraphAddHostNodeFn)dlsym(lib, "cuGraphAddHostNode");
  if (!add) {
    fprintf(stderr, "SKIP dlsym cuGraphAddHostNode: %s\n", dlerror());
    return 77;
  }

  CUDA_HOST_NODE_PARAMS params;
  memset(&params, 0, sizeof(params));
  params.fn = host_cb;
  params.userData = (void *)(uintptr_t)0x12345678;
  CUgraphNode node = 0;

  fprintf(stderr, "CUDA_HOSTNODE callback=%p add=%p params=%p\nMARK add-enter\n",
          (void *)host_cb, (void *)add, (void *)&params);
  fflush(stderr);
  CUresult rc = add(&node, (CUgraph)(uintptr_t)0x1111, NULL, 0, &params);
  fprintf(stderr, "MARK add-return rc=%d callbacks=%u node=%p\n", rc, callback_count, node);
  fflush(stderr);

  if (rc != 0) return 20;
  if (callback_count != 1) return 21;
  if (node != (CUgraphNode)(uintptr_t)0x2222) return 22;
  return 0;
}
