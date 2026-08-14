#include <cerrno>
#include <unistd.h>

extern "C" __attribute__((visibility("default"), noinline)) int native_entered_callback(int entered_fd, int release_fd) {
  const char Entered = 'E';
  ssize_t Written;
  do {
    Written = ::write(entered_fd, &Entered, 1);
  } while (Written < 0 && errno == EINTR);
  if (Written != 1) {
    return -1;
  }

  char Release = 0;
  ssize_t Read;
  do {
    Read = ::read(release_fd, &Release, 1);
  } while (Read < 0 && errno == EINTR);
  if (Read != 1 || Release != 'R') {
    return -2;
  }

  return 1023;
}
