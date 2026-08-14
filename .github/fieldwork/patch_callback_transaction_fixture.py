#!/usr/bin/env python3
from pathlib import Path
import sys


def once(path: Path, old: str, new: str, label: str) -> None:
    s = path.read_text()
    n = s.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected one anchor, found {n}')
    path.write_text(s.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f'usage: {sys.argv[0]} FEX_FULL_THUNK_PAIR_DIR')
    root = Path(sys.argv[1]).resolve()
    makefile = root / 'Makefile'
    src = root / 'guest/callback_transaction.cpp'

    src.write_text(r'''#include <dlfcn.h>
#include <errno.h>
#include <signal.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>

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
  "0xbb,0x41,0xa0,0a,0xa5,0x88,0x05,0x7d,0xd2,0xf2,0x99,0x58,0x8c,0x74,0x25,0x4e,"
  "0xec,0xab,0xd5,0xb9,0x5d,0xc9,0xf5,0xfc,0x60,0x77,0x43,0xe0,0x9a,0x84,0xce,0x08");

using SetGeneration = void (*)(int);
using GetAddress = uintptr_t (*)();

template<typename T>
static T sym(void* h, const char* name) {
  dlerror();
  void* p = dlsym(h, name);
  const char* e = dlerror();
  if (e || !p) {
    std::fprintf(stderr, "TXFIX dlsym(%s): %s\n", name, e ? e : "null");
    std::exit(2);
  }
  return reinterpret_cast<T>(p);
}

static void load_host_thunk_library() {
  struct Args { const char* Name; uintptr_t CallbackThunks; } args {"lifetime", 0};
  fex_builtin_loadlib(&args);
}

static int register_callback(uintptr_t target, uintptr_t unpacker, int x) {
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
    const int rv = fn();
    std::fprintf(stderr, "TXFIX child %s rv=%d\n", label, rv);
    _exit(0);
  }
  int status = 0;
  waitpid(pid, &status, 0);
  if (WIFSIGNALED(status)) {
    const int rc = 128 + WTERMSIG(status);
    std::fprintf(stderr, "TXFIX child %s signal=%d rc=%d\n", label, WTERMSIG(status), rc);
    return rc;
  }
  const int rc = WEXITSTATUS(status);
  std::fprintf(stderr, "TXFIX child %s exit=%d\n", label, rc);
  return rc;
}

int main() {
  load_host_thunk_library();
  void* h = dlopen("./guest/liblifetime-guest.so", RTLD_NOW | RTLD_LOCAL);
  if (!h) {
    std::fprintf(stderr, "TXFIX dlopen: %s\n", dlerror());
    return 3;
  }

  auto set_generation = sym<SetGeneration>(h, "lifetime_set_generation");
  auto target_address = sym<GetAddress>(h, "lifetime_guest_target_address");
  auto unpacker_address = sym<GetAddress>(h, "lifetime_guest_unpacker_address");
  set_generation(7);
  const uintptr_t target = target_address();
  const uintptr_t unpacker = unpacker_address();

  const int first = register_callback(target, unpacker, 5);
  std::fprintf(stderr, "TXFIX initial-callback=%d target=%#lx unpacker=%#lx\n", first, target, unpacker);
  if (first != 70053) return 4;

  // Choose an unaligned address immediately before the target so the FEX
  // retirement range still includes the callback target while the real Linux
  // munmap must fail with EINVAL and leave the mapping untouched.
  uintptr_t bad = target - 1;
  if ((bad & 4095) == 0) --bad;
  errno = 0;
  const int mr = munmap(reinterpret_cast<void*>(bad), 1);
  const int saved_errno = errno;
  std::fprintf(stderr, "TXFIX failed-munmap addr=%#lx rc=%d errno=%d\n", bad, mr, saved_errno);
  if (mr != -1 || saved_errno != EINVAL) return 5;

  const int after_failed = child("after-failed-munmap", [&]() { return call_first_callback(6); });
  std::fprintf(stderr, "TXFIX callback-after-failed-munmap-exit=%d\n", after_failed);

  // Final valid dlclose must still retire the descriptor. The escaped native
  // host trampoline is invoked in a child so deliberate exit=113 is observable.
  if (dlclose(h) != 0) return 6;
  const int after_close = child("after-real-close", [&]() { return call_first_callback(7); });
  std::fprintf(stderr, "TXFIX callback-after-real-close-exit=%d\n", after_close);

  // The workflow interprets the two exits. Return 0 for either implementation
  // as long as the final successful close revoked the old callback.
  if (after_close != 113) return 7;
  std::fprintf(stderr, "TXFIX RESULT after-failed=%d after-close=%d\n", after_failed, after_close);
  return 0;
}
''')

    text = makefile.read_text()
    if 'guest/callback_transaction' not in text:
        text = text.replace(
            'all: guest/liblifetime-guest.so guest/fex_full_lifetime guest/callback_inflight host/lifetime-host.so\n',
            'all: guest/liblifetime-guest.so guest/fex_full_lifetime guest/callback_inflight guest/callback_transaction host/lifetime-host.so\n',
            1,
        )
        text += '''\nguest/callback_transaction: guest/callback_transaction.cpp\n\t$(GUEST_CXX) $(GUEST_CXXFLAGS) -o $@ $< -ldl\n'''
        text = text.replace(
            'rm -f guest/liblifetime-guest.so guest/fex_full_lifetime guest/callback_inflight host/lifetime-host.so',
            'rm -f guest/liblifetime-guest.so guest/fex_full_lifetime guest/callback_inflight guest/callback_transaction host/lifetime-host.so',
            1,
        )
        makefile.write_text(text)

    print('Added callback transaction fixture with real EINVAL munmap rollback check')


if __name__ == '__main__':
    main()
