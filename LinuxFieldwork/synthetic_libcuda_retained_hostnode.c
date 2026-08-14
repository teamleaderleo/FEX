#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

typedef int CUresult;
typedef void *CUgraph;
typedef void *CUgraphNode;
typedef void *CUgraphExec;
typedef void *CUstream;
typedef void (*CUhostFn)(void *userData);
typedef struct CUDA_HOST_NODE_PARAMS_st { CUhostFn fn; void *userData; } CUDA_HOST_NODE_PARAMS;

static CUhostFn retained_fn;
static void *retained_user;

__attribute__((visibility("default")))
CUresult cuGraphAddHostNode(CUgraphNode *phGraphNode, CUgraph hGraph,
                            const CUgraphNode *dependencies, size_t numDependencies,
                            const CUDA_HOST_NODE_PARAMS *nodeParams) {
  fprintf(stderr, "SYNTH_CUDA_RETAIN graph=%p deps=%p count=%zu fn=%p user=%p\n",
          hGraph, (const void*)dependencies, numDependencies,
          nodeParams ? (void*)nodeParams->fn : NULL,
          nodeParams ? nodeParams->userData : NULL);
  fflush(stderr);
  if (!nodeParams || !nodeParams->fn) return 1;
  retained_fn = nodeParams->fn;
  retained_user = nodeParams->userData;
  if (phGraphNode) *phGraphNode = (CUgraphNode)(uintptr_t)0xc0de;
  return 0;
}

__attribute__((visibility("default")))
CUresult cuGraphLaunch(CUgraphExec exec, CUstream stream) {
  fprintf(stderr, "SYNTH_CUDA_LAUNCH exec=%p stream=%p retained_fn=%p user=%p\n",
          exec, stream, (void*)retained_fn, retained_user);
  fflush(stderr);
  if (!retained_fn) return 2;
  retained_fn(retained_user);
  return 0;
}
