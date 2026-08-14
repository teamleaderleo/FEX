#define GL_GLEXT_PROTOTYPES 1
#define GLX_GLXEXT_PROTOTYPES 1

#include <GL/glx.h>
#include <GL/glxext.h>
#include <GL/gl.h>
#include <GL/glext.h>

#undef GL_ARB_viewport_array
#include "../libGL/glcorearb.h"

#include <X11/Xlib.h>
#include <X11/Xutil.h>

#include <cstdlib>

#include "common/Guest.h"
#include "thunkgen_guest_libGL_bridge.inl"

extern "C" void* FEXGLBridgeMalloc(size_t size) {
  return malloc(size);
}

extern "C" uintptr_t FEXGLBridgeMallocUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(FEXGLBridgeMalloc)>::Unpack);
}

extern "C" uintptr_t FEXGLBridgeXSyncUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(XSync)>::Unpack);
}

extern "C" uintptr_t FEXGLBridgeXGetVisualInfoUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(XGetVisualInfo)>::Unpack);
}

extern "C" uintptr_t FEXGLBridgeXDisplayStringUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(XDisplayString)>::Unpack);
}
