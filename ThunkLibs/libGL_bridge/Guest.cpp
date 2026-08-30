#define GL_GLEXT_PROTOTYPES 1
#define GLX_GLXEXT_PROTOTYPES 1

#include <GL/gl.h>
#include <GL/glext.h>
#include <GL/glx.h>
#include <GL/glxext.h>

#undef GL_ARB_viewport_array
#include "../libGL/glcorearb.h"

#include <X11/Xlib.h>
#include <X11/Xutil.h>

#include <cstdlib>

#include "common/Guest.h"
#include "thunkgen_guest_libGL_bridge.inl"

#define FEX_GL_BRIDGE_EXPORT __attribute__((visibility("default")))

// These forwarding targets live beside their unpackers. The companion's X11
// dependency also keeps the implementation they call alive after libGL closes.
extern "C" FEX_GL_BRIDGE_EXPORT void* FEXGLBridgeMalloc(size_t size) {
  return malloc(size);
}

extern "C" FEX_GL_BRIDGE_EXPORT int FEXGLBridgeXSync(Display* display, Bool discard) {
  return XSync(display, discard);
}

extern "C" FEX_GL_BRIDGE_EXPORT XVisualInfo* FEXGLBridgeXGetVisualInfo(Display* display, long mask, XVisualInfo* info, int* count) {
  return XGetVisualInfo(display, mask, info, count);
}

extern "C" FEX_GL_BRIDGE_EXPORT char* FEXGLBridgeXDisplayString(Display* display) {
  return XDisplayString(display);
}

extern "C" FEX_GL_BRIDGE_EXPORT uintptr_t FEXGLBridgeMallocUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(FEXGLBridgeMalloc)>::Unpack);
}

extern "C" FEX_GL_BRIDGE_EXPORT uintptr_t FEXGLBridgeXSyncUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(FEXGLBridgeXSync)>::Unpack);
}

extern "C" FEX_GL_BRIDGE_EXPORT uintptr_t FEXGLBridgeXGetVisualInfoUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(FEXGLBridgeXGetVisualInfo)>::Unpack);
}

extern "C" FEX_GL_BRIDGE_EXPORT uintptr_t FEXGLBridgeXDisplayStringUnpacker() {
  return reinterpret_cast<uintptr_t>(CallbackUnpack<decltype(FEXGLBridgeXDisplayString)>::Unpack);
}
