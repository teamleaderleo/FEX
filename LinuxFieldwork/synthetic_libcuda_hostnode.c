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

__attribute__((visibility("default")))
CUresult cuGraphAddHostNode(CUgraphNode *phGraphNode,
                            CUgraph hGraph,
                            const CUgraphNode *dependencies,
                            size_t numDependencies,
                            const CUDA_HOST_NODE_PARAMS *nodeParams) {
  fprintf(stderr,
          "SYNTH_CUDA_ADD graph=%p deps=%p count=%zu params=%p fn=%p user=%p\n",
          hGraph, (const void *)dependencies, numDependencies, (const void *)nodeParams,
          nodeParams ? (void *)nodeParams->fn : NULL,
          nodeParams ? nodeParams->userData : NULL);
  fflush(stderr);

  if (!nodeParams || !nodeParams->fn) return 1;
  nodeParams->fn(nodeParams->userData);
  if (phGraphNode) *phGraphNode = (CUgraphNode)(uintptr_t)0xc0de;
  return 0;
}
