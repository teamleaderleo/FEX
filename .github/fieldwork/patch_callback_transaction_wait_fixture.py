#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f'usage: {sys.argv[0]} FEX_FULL_THUNK_PAIR_DIR')
    root = Path(sys.argv[1]).resolve()
    makefile = root / 'Makefile'
    src = root / 'guest/callback_transaction_wait.cpp'

    src.write_text(r'''#include <dlfcn.h>
#include <errno.h>
#include <signal.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <thread>

#define DEFINE_MAGIC_THUNK(symbol, bytes) \
  extern "C" __attribute__((visibility("hidden"))) int symbol(void*); \
  asm(".text\n" #symbol ":\n.byte 0x0f, 0x3f\n.byte " bytes "\n")

DEFINE_MAGIC_THUNK(
  fex_builtin_loadlib,
  "0x27,0x7e,0xb7,0x69,0x5b,0xe9,0xab,0x12,0x6e,0xf7,0x85,0x9d,0x4b,0xc9,0xa2,0x44,"
  "0x46,0xcf,0xbd,0xb5,0x87,0x43,0xef,0x28,0xa2,0x65,0xba,0xfc,0x89,0x0f,0x77,0x80");
DEFINE_MAGIC_THUNK(
  lifetime_register_guest_callback_thunk,
  "0xb8,0x47,0xf3,0x6e,0x50,0xd8,0x5d,0x73,0xd6,0xd7,0x83,0x1a,0x7d,0x98,0xb0,0x89,"
  "0xb9,0xaf,0x87,0x81,0xc7,0x75,0x44,0xf1,0x28,0xe0,0xb7,0xbe,0x27,0xe1,0xc8,0x56");
DEFINE_MAGIC_THUNK(
  lifetime_call_first_callback_thunk,
  "0xbb,0x41,0xa0,0x0a,0xa5,0x88,0x05,0x7d,0xd2,0xf2,0x99,0x58,0x8c,0x74,0x25,0x4e,"
  "0xec,0xab,0xd5,0xb9,0x5d,0xc9,0xf5,0xfc,0x60,0x77,0x43,0xe0,0x9a,0x84,0xce,0x08");

using SetGeneration = void (*)(int);
using ConfigureBlock = void (*)(int, int);
using GetAddress = uintptr_t (*)();

template<typename T>
static T sym(void* h, const char* name) {
  dlerror();
  void* p = dlsym(h, name);
  const char* e = dlerror();
  if (e || !p) {
    std::fprintf(stderr, "TXWAIT dlsym(%s): %s\n", name, e ? e : "null");
    std::exit(2);
  }
  return reinterpret_cast<T>(p);
}

static void load_host_thunk_library() {
  struct Args { const char* Name; uintptr_t CallbackThunks; } args {"lifetime", 0};
  fex_builtin_loadlib(&args);
}

static int register_and_call(uintptr_t target, uintptr_t unpacker, int x) {
  struct __attribute__((packed)) Args { uintptr_t target; uintptr_t unpacker; int a0; int rv; } args {target, unpacker, x, 0};
  lifetime_register_guest_callback_thunk(&args);
  return args.rv;
}

static int call_first(int x) {
  struct __attribute__((packed)) Args { int a0; int rv; } args {x, 0};
  lifetime_call_first_callback_thunk(&args);
  return args.rv;
}

template<typename Fn>
static int child(const char* label, Fn&& fn) {
  std::fflush(nullptr);
  pid_t pid = fork();
  if (pid < 0) return 2;
  if (pid == 0) {
    const int rv = fn();
    std::fprintf(stderr, "TXWAIT child %s rv=%d\n", label, rv);
    _exit(0);
  }
  int status = 0;
  waitpid(pid, &status, 0);
  if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
  return WEXITSTATUS(status);
}

int main() {
  load_host_thunk_library();

  int entered[2] {-1, -1};
  int release[2] {-1, -1};
  if (pipe(entered) != 0 || pipe(release) != 0) {
    std::perror("pipe");
    return 2;
  }

  void* h = dlopen("./guest/liblifetime-guest.so", RTLD_NOW | RTLD_LOCAL);
  if (!h) {
    std::fprintf(stderr, "TXWAIT dlopen: %s\n", dlerror());
    return 3;
  }
  auto set_generation = sym<SetGeneration>(h, "lifetime_set_generation");
  auto configure_block = sym<ConfigureBlock>(h, "lifetime_configure_callback_block");
  auto target_address = sym<GetAddress>(h, "lifetime_guest_target_address");
  auto unpacker_address = sym<GetAddress>(h, "lifetime_guest_unpacker_address");
  set_generation(7);
  configure_block(entered[1], release[0]);
  const uintptr_t target = target_address();
  const uintptr_t unpacker = unpacker_address();

  std::atomic<int> a_rv {-99999};
  std::atomic<int> b_rv {-99999};
  std::atomic<int> munmap_rc {-99999};
  std::atomic<int> munmap_errno {-99999};
  std::atomic<bool> b_done {false};
  std::atomic<bool> munmap_done {false};

  std::thread a([&]() {
    a_rv.store(register_and_call(target, unpacker, 5), std::memory_order_release);
    std::fprintf(stderr, "TXWAIT A-returned rv=%d\n", a_rv.load());
  });

  char entered_byte = 0;
  if (read(entered[0], &entered_byte, 1) != 1 || entered_byte != 'E') {
    std::fprintf(stderr, "TXWAIT failed-to-observe-A-entry\n");
    return 4;
  }
  std::fprintf(stderr, "TXWAIT A-entered-host-block\n");

  uintptr_t bad = target - 1;
  if ((bad & 4095) == 0) --bad;
  std::thread unmapper([&]() {
    errno = 0;
    int rc = munmap(reinterpret_cast<void*>(bad), 1);
    int e = errno;
    munmap_rc.store(rc, std::memory_order_release);
    munmap_errno.store(e, std::memory_order_release);
    munmap_done.store(true, std::memory_order_release);
    std::fprintf(stderr, "TXWAIT munmap-returned rc=%d errno=%d\n", rc, e);
  });

  // Give BeginDrain time to observe A as active and block before the host syscall.
  std::this_thread::sleep_for(std::chrono::milliseconds(120));

  std::thread b([&]() {
    b_rv.store(call_first(6), std::memory_order_release);
    b_done.store(true, std::memory_order_release);
    std::fprintf(stderr, "TXWAIT B-returned rv=%d\n", b_rv.load());
  });

  std::this_thread::sleep_for(std::chrono::milliseconds(250));
  std::fprintf(stderr, "TXWAIT before-release munmap-done=%d B-done=%d\n",
               munmap_done.load(std::memory_order_acquire) ? 1 : 0,
               b_done.load(std::memory_order_acquire) ? 1 : 0);

  // First release lets A finish and allows BeginDrain to reach the deliberately
  // failing host munmap. Second release is queued for B after rollback wakes it.
  const char releases[2] {'R', 'R'};
  if (write(release[1], releases, 2) != 2) {
    std::perror("release write");
    return 5;
  }
  std::fprintf(stderr, "TXWAIT released-A-and-queued-B\n");

  a.join();
  unmapper.join();
  b.join();

  const int ar = a_rv.load(std::memory_order_acquire);
  const int br = b_rv.load(std::memory_order_acquire);
  const int mr = munmap_rc.load(std::memory_order_acquire);
  const int me = munmap_errno.load(std::memory_order_acquire);
  std::fprintf(stderr, "TXWAIT joined A=%d B=%d munmap=%d errno=%d\n", ar, br, mr, me);

  if (ar != 70053 || br != 70063 || mr != -1 || me != EINVAL) return 6;

  if (dlclose(h) != 0) return 7;
  const int stale = child("stale-after-close", [&]() { return call_first(7); });
  std::fprintf(stderr, "TXWAIT stale-after-close-exit=%d\n", stale);
  if (stale != 113) return 8;

  std::fprintf(stderr, "TXWAIT PASS\n");
  return 0;
}
''')

    text = makefile.read_text()
    if 'guest/callback_transaction_wait' not in text:
        # This script is applied after patch_callback_inflight_fixture.py.
        text = text.replace(
            'all: guest/liblifetime-guest.so guest/fex_full_lifetime guest/callback_inflight host/lifetime-host.so\n',
            'all: guest/liblifetime-guest.so guest/fex_full_lifetime guest/callback_inflight guest/callback_transaction_wait host/lifetime-host.so\n',
            1,
        )
        text += '''\nguest/callback_transaction_wait: guest/callback_transaction_wait.cpp\n\t$(GUEST_CXX) $(GUEST_CXXFLAGS) -pthread -o $@ $< -ldl\n'''
        text = text.replace(
            'rm -f guest/liblifetime-guest.so guest/fex_full_lifetime guest/callback_inflight host/lifetime-host.so',
            'rm -f guest/liblifetime-guest.so guest/fex_full_lifetime guest/callback_inflight guest/callback_transaction_wait host/lifetime-host.so',
            1,
        )
        makefile.write_text(text)

    print('Added concurrent callback arrival during failed-unmap transaction fixture')


if __name__ == '__main__':
    main()
