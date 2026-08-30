#include <stdio.h>

typedef int (*XErrorHandler)(void*, void*);

int XSync(void* display, int discard) {
  fprintf(stderr, "GUEST_XSYNC display=%p discard=%d\n", display, discard);
  return 0;
}

void* XGetVisualInfo(void* display, long mask, void* info, int* count) {
  (void)display;
  (void)mask;
  (void)info;
  if (count) *count = 0;
  return 0;
}

char* XDisplayString(void* display) {
  fprintf(stderr, "GUEST_XDISPLAYSTRING display=%p\n", display);
  return ":99";
}

XErrorHandler XSetErrorHandler(XErrorHandler handler) {
  return handler;
}
