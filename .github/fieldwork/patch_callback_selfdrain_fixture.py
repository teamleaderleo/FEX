#!/usr/bin/env python3
from pathlib import Path
import sys


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
    makefile = root / 'Makefile'

    # patch_callback_inflight_fixture.py already added dlfcn-capable guest code
    # and the callback registration thunks. Add a self handle used only by this
    # adversarial fixture.
    if '#include <dlfcn.h>' not in guest.read_text():
        once(guest, '#include <cstdint>\n', '#include <cstdint>\n#include <dlfcn.h>\n', 'guest dlfcn include')

    once(
        guest,
        '''static int callback_entered_fd = -1;\nstatic int callback_release_fd = -1;\n''',
        '''static int callback_entered_fd = -1;\nstatic int callback_release_fd = -1;\nstatic void* callback_self_handle;\n\nextern "C" __attribute__((visibility("default"), noinline))\nvoid lifetime_configure_callback_self_close(void* handle) {\n  callback_self_handle = handle;\n}\n''',
        'self-close handle state',
    )

    once(
        guest,
        '''  return generation * 10000 + x * 10 + 3;\n}\n''',
        '''  if (callback_self_handle) {\n    void* handle = callback_self_handle;\n    callback_self_handle = nullptr;\n    // This runs while FEX's host-to-guest callback descriptor is active.\n    // Descriptor-only teardown can unmap the frame. A drain that waits for\n    // Active==0 can instead wait on the lease held by this same thread.\n    const int close_rc = dlclose(handle);\n    if (close_rc != 0) {\n      return -8000 + close_rc;\n    }\n  }\n  return generation * 10000 + x * 10 + 3;\n}\n''',
        'self-close in callback target',
    )

    race = root / 'guest/callback_selfdrain.cpp'
    race.write_text(r'''#include <dlfcn.h>
#include <cstdio>
#include <cstdlib>
#include <cstdint>

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

using SetGeneration = void (*)(int);
using ConfigureSelfClose = void (*)(void*);
using GetAddress = uintptr_t (*)();

template<typename T>
static T sym(void* h, const char* name) {
  dlerror();
  void* p = dlsym(h, name);
  const char* e = dlerror();
  if (e || !p) {
    std::fprintf(stderr, "SELF_DRAIN dlsym(%s): %s\n", name, e ? e : "null");
    std::exit(2);
  }
  return reinterpret_cast<T>(p);
}

static void load_host_thunk_library() {
  struct Args { const char* Name; uintptr_t CallbackThunks; } args {"lifetime", 0};
  fex_builtin_loadlib(&args);
}

static int register_and_call(uintptr_t target, uintptr_t unpacker, int x) {
  struct __attribute__((packed)) Args {
    uintptr_t target;
    uintptr_t unpacker;
    int a0;
    int rv;
  } args {target, unpacker, x, -99999};
  std::fprintf(stderr, "SELF_DRAIN invoke target=%#lx unpacker=%#lx\n", target, unpacker);
  lifetime_register_guest_callback_thunk(&args);
  std::fprintf(stderr, "SELF_DRAIN returned rv=%d\n", args.rv);
  return args.rv;
}

int main() {
  setvbuf(stderr, nullptr, _IONBF, 0);
  load_host_thunk_library();

  void* h = dlopen("./guest/liblifetime-guest.so", RTLD_NOW | RTLD_LOCAL);
  if (!h) {
    std::fprintf(stderr, "SELF_DRAIN dlopen: %s\n", dlerror());
    return 3;
  }

  auto set_generation = sym<SetGeneration>(h, "lifetime_set_generation");
  auto configure_self = sym<ConfigureSelfClose>(h, "lifetime_configure_callback_self_close");
  auto target_address = sym<GetAddress>(h, "lifetime_guest_target_address");
  auto unpacker_address = sym<GetAddress>(h, "lifetime_guest_unpacker_address");

  set_generation(8);
  const uintptr_t target = target_address();
  const uintptr_t unpacker = unpacker_address();
  configure_self(h);
  std::fprintf(stderr, "SELF_DRAIN configured handle=%p target=%#lx unpacker=%#lx\n", h, target, unpacker);

  const int rv = register_and_call(target, unpacker, 5);
  std::fprintf(stderr, "SELF_DRAIN survived rv=%d\n", rv);
  return rv == 80053 ? 0 : 4;
}
''')

    once(
        makefile,
        '''all: guest/fex_full_lifetime host/libfex_thunk_host-lifetime.so\n''',
        '''all: guest/fex_full_lifetime guest/callback_selfdrain host/libfex_thunk_host-lifetime.so\n''',
        'make all selfdrain',
    )
    makefile.write_text(makefile.read_text() + '''\n
guest/callback_selfdrain: guest/callback_selfdrain.cpp
\t$(GUEST_CXX) -std=c++20 -O2 -g -Wall -Wextra -Wpedantic $< -ldl -o $@
''')

    print(race)


if __name__ == '__main__':
    main()
