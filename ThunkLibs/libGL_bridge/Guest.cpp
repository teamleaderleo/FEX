// SPDX-License-Identifier: MIT
#define GL_GLEXT_PROTOTYPES 1
#define GLX_GLXEXT_PROTOTYPES 1

#include <GL/glx.h>
#include <GL/glxext.h>
#include <GL/gl.h>
#include <GL/glext.h>

#undef GL_ARB_viewport_array
#include "glcorearb.h"

#include <cstdlib>
#include <X11/Xutil.h>
#include "common/Guest.h"
#include "thunkgen_bridge_libGL.inl"

extern "C" void* FEXGLBridgeMalloc(size_t size) {
  return std::malloc(size);
}
extern "C" uintptr_t fex_gl_bridge_malloc_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(FEXGLBridgeMalloc)>::Unpack);
}
extern "C" uintptr_t fex_gl_bridge_xsync_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XSync)>::Unpack);
}
extern "C" uintptr_t fex_gl_bridge_xgetvisualinfo_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XGetVisualInfo)>::Unpack);
}
extern "C" uintptr_t fex_gl_bridge_xdisplaystring_unpacker() {
  return reinterpret_cast<uintptr_t>(&CallbackUnpack<decltype(XDisplayString)>::Unpack);
}
