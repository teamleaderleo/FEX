#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

typedef int CUresult;
typedef void *CUgraph;
typedef void *CUgraphNode;
typedef void (*CUhostFn)(void *userData);

typedef struct CUDA_HOST_NODE_PARAMS_st {
  CUhostFn fn;
  void *userData;
} CUDA_HOST_NODE_PARAMS;

typedef CUresult (*cuGraphAddHostNodeFn)(CUgraphNode *phGraphNode,
                                         CUgraph hGraph,
                                         const CUgraphNode *dependencies,
                                         size_t numDependencies,
                                         const CUDA_HOST_NODE_PARAMS *nodeParams);

static volatile unsigned callback_count;

__attribute__((used, noinline))
static void host_node_body(void *userData) {
  ++callback_count;
  fprintf(stderr, "CUDA_HOST_CALLBACK count=%u user=%p\n", callback_count, userData);
  fflush(stderr);
}

#if defined(__x86_64__)
__attribute__((used, naked, noinline))
static void host_node_callback(void *userData) {
  (void)userData;
  __asm__ volatile(".byte 0xe9,0,0,0,0\n\tjmp host_node_body");
}
#else
static void host_node_callback(void *userData) {
  host_node_body(userData);
}
#endif

int main(void) {
  void *lib = dlopen("libcuda.so.1", RTLD_NOW | RTLD_LOCAL);
  if (!lib) {
    fprintf(stderr, "SKIP dlopen libcuda.so.1: %s\n", dlerror());
    return 77;
  }

  cuGraphAddHostNodeFn add_host = (cuGraphAddHostNodeFn)dlsym(lib, "cuGraphAddHostNode");
  if (!add_host) {
    fprintf(stderr, "SKIP dlsym cuGraphAddHostNode: %s\n", dlerror());
    return 77;
  }

  CUDA_HOST_NODE_PARAMS params = {
    .fn = host_node_callback,
    .userData = (void *)(uintptr_t)0x12345678,
  };
  CUgraphNode node = NULL;

  fprintf(stderr, "CUDA_PROBE callback=%p add_host=%p params=%p\nMARK add-enter\n",
          (void *)host_node_callback, (void *)add_host, (void *)&params);
  fflush(stderr);

  CUresult rc = add_host(&node, (CUgraph)(uintptr_t)0x1111, NULL, 0, &params);

  fprintf(stderr, "MARK add-return rc=%d node=%p callbacks=%u\n", rc, node, callback_count);
  fflush(stderr);

  if (rc != 0) return 20;
  if (node != (CUgraphNode)(uintptr_t)0xc0de) return 21;
  if (callback_count != 1) return 22;
  return 0;
}
