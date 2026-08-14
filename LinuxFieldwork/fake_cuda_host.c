#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

typedef int CUresult;
typedef void *CUgraph;
typedef void *CUgraphNode;
typedef void (*CUhostFn)(void *);
typedef struct CUDA_HOST_NODE_PARAMS_st {
  CUhostFn fn;
  void *userData;
} CUDA_HOST_NODE_PARAMS;

__attribute__((visibility("default")))
CUresult cuGraphAddHostNode(CUgraphNode *node, CUgraph graph, const CUgraphNode *deps,
                            size_t num_deps, const CUDA_HOST_NODE_PARAMS *params) {
  (void)graph;
  (void)deps;
  (void)num_deps;
  fprintf(stderr, "CUDA_STUB add-enter params=%p fn=%p user=%p\n",
          (void *)params, params ? (void *)params->fn : NULL, params ? params->userData : NULL);
  fflush(stderr);
  if (params && params->fn) {
    params->fn(params->userData);
  }
  if (node) {
    *node = (CUgraphNode)(uintptr_t)0x2222;
  }
  fprintf(stderr, "CUDA_STUB add-return\n");
  fflush(stderr);
  return 0;
}
