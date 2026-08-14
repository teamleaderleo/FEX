/*
$info$
tags: thunklibs|GL
desc: Handles glXGetProcAddress
$end_info$
*/

#define GL_GLEXT_PROTOTYPES 1
#define GLX_GLXEXT_PROTOTYPES 1

#include <GL/glx.h>
#include <GL/glxext.h>
#include <GL/gl.h>
#include <GL/glext.h>

#undef GL_ARB_viewport_array
#include "glcorearb.h"

#include <dlfcn.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <string_view>
#include <unordered_map>

#include "common/Guest.h"
#include "thunkgen_guest_libGL_bridge_accessors.inl"

#define AllocateHostTrampolineForGuestFunction FEXAllocateResidentHostTrampolineForGuestFunction
#include "thunkgen_guest_libGL.inl"
#undef AllocateHostTrampolineForGuestFunction

typedef void voidFunc();

extern "C" void* FEXGLBridgeMalloc(size_t);
extern "C" uintptr_t FEXGLBridgeMallocUnpacker();
extern "C" uintptr_t FEXGLBridgeXSyncUnpacker();
extern "C" uintptr_t FEXGLBridgeXGetVisualInfoUnpacker();
extern "C" uintptr_t FEXGLBridgeXDisplayStringUnpacker();

// Maps OpenGL API function names to process-resident guest bridge functions
// linked to the corresponding host function pointer.
const std::unordered_map<std::string_view, uintptr_t /* guest function address */> HostPtrInvokers = std::invoke([]() {
#define PAIR(name, unused) Ret[#name] = reinterpret_cast<uintptr_t>(FEXGetResidentCallerForHostFunction(name));
  std::unordered_map<std::string_view, uintptr_t> Ret;
  FOREACH_internal_SYMBOL(PAIR);
  return Ret;
#undef PAIR
});

extern "C" {
voidFunc* glXGetProcAddress(const GLubyte* procname) {
  auto Ret = fexfn_pack_glXGetProcAddress(procname);
  if (!Ret) {
    return nullptr;
  }

  auto TargetFuncIt = HostPtrInvokers.find(reinterpret_cast<const char*>(procname));
  if (TargetFuncIt == HostPtrInvokers.end()) {
    std::string_view procname_s {reinterpret_cast<const char*>(procname)};
    // If glXGetProcAddress is querying itself, then we can just return itself.
    // Some games do this for unknown reasons.
    if (procname_s == "glXGetProcAddress" || procname_s == "glXGetProcAddressARB") {
      return reinterpret_cast<voidFunc*>(glXGetProcAddress);
    }

    // Extension found in host but not in our interface definition => Not fatal but warn about it
    // Some games query leaked GLES symbols but don't use them
    fprintf(stderr, "glXGetProcAddress: not found %s\n", procname);
    return nullptr;
  }

  LinkAddressToFunction((uintptr_t)Ret, TargetFuncIt->second);
  return Ret;
}

voidFunc* glXGetProcAddressARB(const GLubyte* procname) {
  return glXGetProcAddress(procname);
}
}

static void OnInit() {
  // GL publishes malloc_wrapper itself to persistent host state, so both the
  // guest target and its unpacker must be resident; moving only the unpacker
  // would still leave a dead target after libGL-guest physically unloads.
  fexfn_pack_GL_SetGuestMalloc((uintptr_t)FEXGLBridgeMalloc, FEXGLBridgeMallocUnpacker());
  fexfn_pack_GL_SetGuestXSync((uintptr_t)XSync, FEXGLBridgeXSyncUnpacker());
  fexfn_pack_GL_SetGuestXGetVisualInfo((uintptr_t)XGetVisualInfo, FEXGLBridgeXGetVisualInfoUnpacker());
  fexfn_pack_GL_SetGuestXDisplayString((uintptr_t)XDisplayString, FEXGLBridgeXDisplayStringUnpacker());
}

// libGL.so must pull in libX11.so as a dependency. Referencing some libX11
// symbol here prevents the linker from optimizing away the unused dependency
auto implicit_libx11_dependency = XSetErrorHandler;

LOAD_LIB_INIT(libGL, OnInit)
