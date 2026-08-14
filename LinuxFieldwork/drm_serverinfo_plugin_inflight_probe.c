#define _GNU_SOURCE
#include <xf86drm.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int addr_mapped(uintptr_t addr) {
  FILE *f = fopen("/proc/self/maps", "r");
  if (!f) return -1;
  char line[4096];
  while (fgets(line, sizeof(line), f)) {
    unsigned long long lo = 0, hi = 0;
    if (sscanf(line, "%llx-%llx", &lo, &hi) == 2 && addr >= (uintptr_t)lo && addr < (uintptr_t)hi) {
      fclose(f);
      return 1;
    }
  }
  fclose(f);
  return 0;
}

typedef void (*ConfigureFn)(int, int);
typedef int (*LoadModuleFn)(const char *);

struct worker_state {
  volatile int returned;
  int fd;
};

static void *open_worker(void *opaque) {
  struct worker_state *s = (struct worker_state *)opaque;
  fprintf(stderr, "DRM_PLUGIN_PROBE open-enter\n"); fflush(stderr);
  s->fd = drmOpen("fex-intentionally-missing-drm-driver", NULL);
  s->returned = 1;
  fprintf(stderr, "DRM_PLUGIN_PROBE open-return fd=%d\n", s->fd); fflush(stderr);
  return NULL;
}

int main(void) {
  int entered[2] = {-1,-1};
  int release[2] = {-1,-1};
  if (pipe(entered) || pipe(release)) { perror("pipe"); return 2; }

  void *plugin = dlopen("./libdrm-callback-plugin.so", RTLD_NOW | RTLD_LOCAL);
  if (!plugin) { fprintf(stderr, "plugin dlopen: %s\n", dlerror()); return 3; }
  ConfigureFn configure = (ConfigureFn)dlsym(plugin, "drm_plugin_configure");
  LoadModuleFn callback = (LoadModuleFn)dlsym(plugin, "drm_plugin_load_module");
  if (!configure || !callback) { fprintf(stderr, "plugin dlsym failed\n"); return 4; }
  configure(entered[1], release[0]);

  drmServerInfo info;
  memset(&info, 0, sizeof(info));
  info.load_module = callback;
  fprintf(stderr, "DRM_PLUGIN_PROBE set-info callback=%p mapped=%d\n", (void*)callback, addr_mapped((uintptr_t)callback));
  fflush(stderr);
  drmSetServerInfo(&info);

  struct worker_state worker = {.returned = 0, .fd = -999};
  pthread_t thread;
  if (pthread_create(&thread, NULL, open_worker, &worker) != 0) { perror("pthread_create"); return 5; }

  char b = 0;
  if (read(entered[0], &b, 1) != 1 || b != 'E') {
    fprintf(stderr, "DRM_PLUGIN_PROBE callback-entry-missing\n");
    return 6;
  }
  fprintf(stderr, "DRM_PLUGIN_PROBE callback-blocked mapped-before-close=%d\n", addr_mapped((uintptr_t)callback));
  fflush(stderr);

  int close_rc = dlclose(plugin);
  fprintf(stderr, "DRM_PLUGIN_PROBE plugin-close rc=%d mapped-after-close=%d worker-returned=%d\n",
          close_rc, addr_mapped((uintptr_t)callback), worker.returned);
  fflush(stderr);

  const char release_byte = 'R';
  if (write(release[1], &release_byte, 1) != 1) { perror("release"); return 7; }
  fprintf(stderr, "DRM_PLUGIN_PROBE released\n"); fflush(stderr);

  pthread_join(thread, NULL);
  fprintf(stderr, "DRM_PLUGIN_PROBE joined fd=%d mapped-after-join=%d\n", worker.fd, addr_mapped((uintptr_t)callback));
  fflush(stderr);
  return 0;
}
