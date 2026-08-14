#!/usr/bin/env python3
from pathlib import Path
import sys

BLOCK_HASH = '"0x21,0x69,0x2d,0x8e,0x96,0x2e,0x62,0x24,0x00,0x8c,0x1f,0x2a,0x81,0xe3,0x58,0x04,"\n  "0xbd,0xe6,0xba,0xe4,0x0d,0x59,0x6c,0x21,0x98,0xdd,0x5d,0xfb,0x0a,0x74,0xdd,0xcf"'


def once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected one anchor, found {n}')
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f'usage: {sys.argv[0]} FEX_FULL_THUNK_PAIR_DIR')
    root = Path(sys.argv[1]).resolve()
    guest = root / 'guest/guest_dso.cpp'
    host = root / 'host/host_dso.cpp'
    makefile = root / 'Makefile'

    # Add one host thunk that blocks while the guest callback frame remains active.
    once(
        guest,
        '''DEFINE_MAGIC_THUNK(
  lifetime_invoke_host_int_thunk,
  "0x99,0x4e,0x95,0x3f,0x95,0xbc,0x4c,0x4f,0x7b,0x17,0x09,0xee,0x98,0x04,0x8a,0xc9,"
  "0x6c,0x55,0x4e,0xc1,0x87,0x07,0x05,0xc9,0xe4,0x4e,0xb1,0x31,0xe3,0x37,0xc5,0x93");
''',
        '''DEFINE_MAGIC_THUNK(
  lifetime_invoke_host_int_thunk,
  "0x99,0x4e,0x95,0x3f,0x95,0xbc,0x4c,0x4f,0x7b,0x17,0x09,0xee,0x98,0x04,0x8a,0xc9,"
  "0x6c,0x55,0x4e,0xc1,0x87,0x07,0x05,0xc9,0xe4,0x4e,0xb1,0x31,0xe3,0x37,0xc5,0x93");

// sha256("lifetime:block_callback")
DEFINE_MAGIC_THUNK(
  lifetime_block_callback_thunk,
  ''' + BLOCK_HASH + ''');
''',
        'guest blocking thunk marker',
    )

    once(
        guest,
        'static int generation;\n',
        '''static int generation;
static int callback_entered_fd = -1;
static int callback_release_fd = -1;

extern "C" __attribute__((visibility("default"), noinline))
void lifetime_configure_callback_block(int entered_fd, int release_fd) {
  callback_entered_fd = entered_fd;
  callback_release_fd = release_fd;
}
''',
        'guest block fd state',
    )

    once(
        guest,
        '''int lifetime_guest_target(int x) {
  return generation * 10000 + x * 10 + 3;
}
''',
        '''int lifetime_guest_target(int x) {
  if (callback_entered_fd >= 0 && callback_release_fd >= 0) {
    struct __attribute__((packed)) BlockArgs {
      int entered_fd;
      int release_fd;
      int rv;
    } args {callback_entered_fd, callback_release_fd, -1};
    lifetime_block_callback_thunk(&args);
    if (args.rv != 0) {
      return -9000 + args.rv;
    }
  }
  return generation * 10000 + x * 10 + 3;
}
''',
        'guest target host-block call',
    )

    # Host implementation of the blocking thunk. It writes an entered byte, then
    # blocks until the controller writes a release byte. File descriptors are
    # process-global, so the native host thunk can use the guest-created pipes.
    once(host, '#include <cstdio>\n', '#include <cstdio>\n#include <cerrno>\n#include <unistd.h>\n', 'host syscall includes')
    once(
        host,
        '''struct __attribute__((packed)) CallCallbackArgs {
  int a0;
  int rv;
};
''',
        '''struct __attribute__((packed)) CallCallbackArgs {
  int a0;
  int rv;
};

struct __attribute__((packed)) BlockCallbackArgs {
  int entered_fd;
  int release_fd;
  int rv;
};
''',
        'host block args',
    )
    once(
        host,
        '''static void host_invoke_int(void* argsv) {
  auto* args = reinterpret_cast<InvokeHostArgs*>(argsv);
  auto fn = reinterpret_cast<int (*)(int)>(static_cast<uintptr_t>(args->host_address));
  args->rv = fn(args->a0);
}
''',
        '''static void host_invoke_int(void* argsv) {
  auto* args = reinterpret_cast<InvokeHostArgs*>(argsv);
  auto fn = reinterpret_cast<int (*)(int)>(static_cast<uintptr_t>(args->host_address));
  args->rv = fn(args->a0);
}

static void host_block_callback(void* argsv) {
  auto* args = reinterpret_cast<BlockCallbackArgs*>(argsv);
  const char entered = 'E';
  ssize_t w;
  do { w = write(args->entered_fd, &entered, 1); } while (w < 0 && errno == EINTR);
  if (w != 1) { args->rv = -1; return; }

  char release = 0;
  ssize_t r;
  do { r = read(args->release_fd, &release, 1); } while (r < 0 && errno == EINTR);
  args->rv = (r == 1 && release == 'R') ? 0 : -2;
}
''',
        'host blocking implementation',
    )
    once(
        host,
        '''static uint8_t hash_register_callback[32] = {
''',
        '''static uint8_t hash_block_callback[32] = {
  0x21,0x69,0x2d,0x8e,0x96,0x2e,0x62,0x24,0x00,0x8c,0x1f,0x2a,0x81,0xe3,0x58,0x04,
  0xbd,0xe6,0xba,0xe4,0x0d,0x59,0x6c,0x21,0x98,0xdd,0x5d,0xfb,0x0a,0x74,0xdd,0xcf};
static uint8_t hash_register_callback[32] = {
''',
        'host block hash',
    )
    once(
        host,
        '''  {hash_invoke_host, host_invoke_int},
  {hash_register_callback, host_register_guest_callback},
''',
        '''  {hash_invoke_host, host_invoke_int},
  {hash_block_callback, host_block_callback},
  {hash_register_callback, host_register_guest_callback},
''',
        'host block export',
    )

    race = root / 'guest/callback_inflight.cpp'
    race.write_text(r'''#include <dlfcn.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
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
    std::fprintf(stderr, "dlsym(%s): %s\n", name, e ? e : "null");
    std::exit(2);
  }
  return reinterpret_cast<T>(p);
}

static void load_host_thunk_library() {
  struct Args { const char* Name; uintptr_t CallbackThunks; } args {"lifetime", 0};
  fex_builtin_loadlib(&args);
}

static int register_and_call_guest_callback(uintptr_t target, uintptr_t unpacker, int x) {
  struct __attribute__((packed)) Args { uintptr_t target; uintptr_t unpacker; int a0; int rv; } args {target, unpacker, x, 0};
  lifetime_register_guest_callback_thunk(&args);
  return args.rv;
}

static int call_first_callback(int x) {
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
    volatile int rv = fn();
    std::fprintf(stderr, "INFLIGHT child %s rv=%d\n", label, rv);
    _exit(0);
  }
  int status = 0;
  waitpid(pid, &status, 0);
  if (WIFSIGNALED(status)) {
    const int rc = 128 + WTERMSIG(status);
    std::fprintf(stderr, "INFLIGHT child %s signal=%d rc=%d\n", label, WTERMSIG(status), rc);
    return rc;
  }
  const int rc = WEXITSTATUS(status);
  std::fprintf(stderr, "INFLIGHT child %s exit=%d\n", label, rc);
  return rc;
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
    std::fprintf(stderr, "dlopen guest: %s\n", dlerror());
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

  std::atomic<int> worker_result {-99999};
  std::atomic<int> close_result {-99999};
  std::atomic<bool> close_done {false};

  std::thread worker([&]() {
    worker_result.store(register_and_call_guest_callback(target, unpacker, 5), std::memory_order_release);
    std::fprintf(stderr, "INFLIGHT worker-returned rv=%d\n", worker_result.load());
  });

  char entered_byte = 0;
  if (read(entered[0], &entered_byte, 1) != 1 || entered_byte != 'E') {
    std::fprintf(stderr, "INFLIGHT failed-to-observe-callback-entry\n");
    return 4;
  }
  std::fprintf(stderr, "INFLIGHT callback-entered-host-block\n");

  std::thread closer([&]() {
    const int rc = dlclose(h);
    close_result.store(rc, std::memory_order_release);
    close_done.store(true, std::memory_order_release);
    std::fprintf(stderr, "INFLIGHT dlclose-returned rc=%d\n", rc);
  });

  std::this_thread::sleep_for(std::chrono::milliseconds(300));
  const bool done_before_release = close_done.load(std::memory_order_acquire);
  std::fprintf(stderr, "INFLIGHT close-done-before-release=%d\n", done_before_release ? 1 : 0);

  const char release_byte = 'R';
  if (write(release[1], &release_byte, 1) != 1) {
    std::perror("release write");
    return 5;
  }
  std::fprintf(stderr, "INFLIGHT released-host-block\n");

  worker.join();
  closer.join();

  const int worker_rv = worker_result.load(std::memory_order_acquire);
  const int close_rv = close_result.load(std::memory_order_acquire);
  std::fprintf(stderr, "INFLIGHT joined worker=%d close=%d\n", worker_rv, close_rv);

  // With a real drain, dlclose must still be blocked before release, the callback
  // must complete normally, and the escaped old trampoline must be revoked.
  if (done_before_release) {
    std::fprintf(stderr, "INFLIGHT UNSAFE_CLOSE_WON_RACE\n");
    return 40;
  }
  if (worker_rv != 70053 || close_rv != 0) {
    std::fprintf(stderr, "INFLIGHT unexpected-results\n");
    return 41;
  }
  const int stale_rc = child("stale-first-callback", [&]() { return call_first_callback(6); });
  if (stale_rc != 113) {
    std::fprintf(stderr, "INFLIGHT stale-callback-not-revoked rc=%d\n", stale_rc);
    return 42;
  }
  std::fprintf(stderr, "INFLIGHT DRAIN_PASS\n");
  return 0;
}
''')

    text = makefile.read_text()
    text = text.replace(
        'all: guest/liblifetime-guest.so guest/fex_full_lifetime host/lifetime-host.so\n',
        'all: guest/liblifetime-guest.so guest/fex_full_lifetime guest/callback_inflight host/lifetime-host.so\n',
        1,
    )
    text += '''\nguest/callback_inflight: guest/callback_inflight.cpp\n\t$(GUEST_CXX) $(GUEST_CXXFLAGS) -pthread -o $@ $< -ldl\n'''
    text = text.replace(
        'rm -f guest/liblifetime-guest.so guest/fex_full_lifetime host/lifetime-host.so',
        'rm -f guest/liblifetime-guest.so guest/fex_full_lifetime guest/callback_inflight host/lifetime-host.so',
        1,
    )
    makefile.write_text(text)

    print('Patched retained thunk fixture with deterministic in-flight callback host-block race')


if __name__ == '__main__':
    main()
