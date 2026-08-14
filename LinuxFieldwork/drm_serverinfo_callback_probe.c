#include <xf86drm.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

static volatile unsigned load_count;

__attribute__((used,noinline)) static int load_module_body(const char *name) {
  ++load_count;
  fprintf(stderr, "DRM_SERVER_CALLBACK count=%u name=%s\n", load_count, name ? name : "(null)");
  fflush(stderr);
  return 0;
}

#if defined(__x86_64__)
__attribute__((used,naked,noinline)) static int load_module_cb(const char *name) {
  (void)name;
  __asm__ volatile(".byte 0xe9,0,0,0,0\n\tjmp load_module_body");
}
#else
static int load_module_cb(const char *name) { return load_module_body(name); }
#endif

typedef void (*SetServerInfoFn)(drmServerInfoPtr);
typedef int (*DrmOpenFn)(const char *, const char *);
typedef int (*DrmAvailableFn)(void);

int main(void) {
  void *lib = dlopen("libdrm.so.2", RTLD_NOW | RTLD_LOCAL);
  if (!lib) { fprintf(stderr, "SKIP dlopen: %s\n", dlerror()); return 77; }
  SetServerInfoFn set_info = (SetServerInfoFn)dlsym(lib, "drmSetServerInfo");
  DrmOpenFn open_fn = (DrmOpenFn)dlsym(lib, "drmOpen");
  DrmAvailableFn available_fn = (DrmAvailableFn)dlsym(lib, "drmAvailable");
  if (!set_info || !open_fn || !available_fn) { fprintf(stderr, "SKIP missing symbol\n"); return 77; }

  int available = available_fn();
  fprintf(stderr, "DRM_SERVER_PRE available=%d callback=%p\n", available, (void *)load_module_cb);
  fflush(stderr);
  if (available) {
    fprintf(stderr, "SKIP hosted DRM is available; load_module path not deterministic\n");
    return 77;
  }

  drmServerInfo info;
  memset(&info, 0, sizeof(info));
  info.load_module = load_module_cb;

  fprintf(stderr, "MARK set-info-enter\n"); fflush(stderr);
  set_info(&info);
  fprintf(stderr, "MARK set-info-return count=%u\n", load_count); fflush(stderr);

  fprintf(stderr, "MARK open-enter\n"); fflush(stderr);
  int fd = open_fn("fex-intentionally-missing-drm-driver", NULL);
  fprintf(stderr, "MARK open-return fd=%d callbacks=%u\n", fd, load_count); fflush(stderr);
  if (fd >= 0) return 20;
  if (load_count != 1) return 21;
  return 0;
}
