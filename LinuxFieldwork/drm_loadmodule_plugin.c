#define _GNU_SOURCE
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>

static int entered_fd = -1;
static int release_fd = -1;

__attribute__((visibility("default")))
void drm_plugin_configure(int entered, int release) {
  entered_fd = entered;
  release_fd = release;
}

__attribute__((visibility("default"), noinline))
int drm_plugin_load_module(const char *name) {
  fprintf(stderr, "DRM_PLUGIN callback-enter name=%s self=%p\n", name ? name : "(null)", (void*)drm_plugin_load_module);
  fflush(stderr);
  if (entered_fd < 0 || release_fd < 0) return -90;
  const char entered = 'E';
  ssize_t w;
  do { w = write(entered_fd, &entered, 1); } while (w < 0 && errno == EINTR);
  if (w != 1) return -91;
  char release = 0;
  ssize_t r;
  do { r = read(release_fd, &release, 1); } while (r < 0 && errno == EINTR);
  fprintf(stderr, "DRM_PLUGIN callback-resume byte=%d\n", (int)release);
  fflush(stderr);
  if (r != 1 || release != 'R') return -92;
  fprintf(stderr, "DRM_PLUGIN callback-return\n");
  fflush(stderr);
  return 0;
}
