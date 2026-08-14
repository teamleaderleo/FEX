#include <xf86drm.h>
#include <drm.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static volatile unsigned callback_count;

__attribute__((used,noinline))
static void vblank_body(int fd, unsigned sequence, unsigned tv_sec, unsigned tv_usec, void *user_data) {
  ++callback_count;
  fprintf(stderr,
          "DRM_CALLBACK count=%u fd=%d sequence=%u tv=%u.%u user=%p\n",
          callback_count, fd, sequence, tv_sec, tv_usec, user_data);
  fflush(stderr);
}

#if defined(__x86_64__)
__attribute__((used,naked,noinline))
static void vblank_cb(int fd, unsigned sequence, unsigned tv_sec, unsigned tv_usec, void *user_data) {
  (void)fd; (void)sequence; (void)tv_sec; (void)tv_usec; (void)user_data;
  __asm__ volatile(".byte 0xe9,0,0,0,0\n\tjmp vblank_body");
}
#else
static void vblank_cb(int fd, unsigned sequence, unsigned tv_sec, unsigned tv_usec, void *user_data) {
  vblank_body(fd, sequence, tv_sec, tv_usec, user_data);
}
#endif

typedef int (*drmHandleEventFn)(int, drmEventContextPtr);

int main(void) {
  void *lib = dlopen("libdrm.so.2", RTLD_NOW | RTLD_LOCAL);
  if (!lib) {
    fprintf(stderr, "SKIP dlopen libdrm.so.2: %s\n", dlerror());
    return 77;
  }
  drmHandleEventFn handle = (drmHandleEventFn)dlsym(lib, "drmHandleEvent");
  if (!handle) {
    fprintf(stderr, "SKIP dlsym drmHandleEvent: %s\n", dlerror());
    return 77;
  }

  int p[2];
  if (pipe(p) != 0) return 70;

  struct drm_event_vblank ev;
  memset(&ev, 0, sizeof(ev));
  ev.base.type = DRM_EVENT_VBLANK;
  ev.base.length = sizeof(ev);
  ev.user_data = UINT64_C(0x12345678);
  ev.tv_sec = 11;
  ev.tv_usec = 22;
  ev.sequence = 33;

  ssize_t written = write(p[1], &ev, sizeof(ev));
  close(p[1]);
  if (written != (ssize_t)sizeof(ev)) return 71;

  drmEventContext ctx;
  memset(&ctx, 0, sizeof(ctx));
  ctx.version = DRM_EVENT_CONTEXT_VERSION;
  ctx.vblank_handler = vblank_cb;

  fprintf(stderr,
          "DRM_PROBE callback=%p handle=%p version=%d event_size=%zu\nMARK handle-enter\n",
          (void *)vblank_cb, (void *)handle, ctx.version, sizeof(ev));
  fflush(stderr);

  int rc = handle(p[0], &ctx);
  close(p[0]);

  fprintf(stderr, "MARK handle-return rc=%d callbacks=%u\n", rc, callback_count);
  fflush(stderr);
  if (rc != 0) return 20;
  if (callback_count != 1) return 21;
  return 0;
}
