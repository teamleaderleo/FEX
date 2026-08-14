#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_FULL_THUNK_PAIR_DIR")

    root = Path(sys.argv[1]).resolve()
    src = root / "guest/callback_mapping_mutation.cpp"
    makefile = root / "Makefile"

    src.write_text(r'''#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <signal.h>
#include <sys/ipc.h>
#include <sys/mman.h>
#include <sys/shm.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

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

using GetAddress = uintptr_t (*)();

static constexpr size_t Page = 4096;

static void die(const char* what) {
  std::perror(what);
  std::exit(2);
}

static void load_host_thunk_library() {
  struct Args { const char* Name; uintptr_t CallbackThunks; } args {"lifetime", 0};
  fex_builtin_loadlib(&args);
}

template<typename T>
static T sym(void* h, const char* name) {
  dlerror();
  void* p = dlsym(h, name);
  const char* e = dlerror();
  if (e || !p) {
    std::fprintf(stderr, "MAPMUT dlsym(%s): %s\n", name, e ? e : "null");
    std::exit(3);
  }
  return reinterpret_cast<T>(p);
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

static int child_call_first() {
  std::fflush(nullptr);
  pid_t pid = fork();
  if (pid < 0) die("fork");
  if (pid == 0) {
    int rv = call_first(99);
    std::fprintf(stderr, "MAPMUT child callback-returned rv=%d\n", rv);
    _exit(42);
  }
  int status = 0;
  if (waitpid(pid, &status, 0) != pid) die("waitpid");
  if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
  return WEXITSTATUS(status);
}

static void write_return(void* page, int value) {
  auto* p = reinterpret_cast<unsigned char*>(page);
  p[0] = 0xB8; // mov eax, imm32
  uint32_t v = static_cast<uint32_t>(value);
  std::memcpy(p + 1, &v, sizeof(v));
  p[5] = 0xC3; // ret
  __builtin___clear_cache(reinterpret_cast<char*>(p), reinterpret_cast<char*>(p + 6));
}

static void* map_exec(size_t size) {
  void* p = mmap(nullptr, size, PROT_READ | PROT_WRITE | PROT_EXEC,
                 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (p == MAP_FAILED) die("mmap exec");
  return p;
}

static int expect_stale_113(const char* mode) {
  int stale = child_call_first();
  std::fprintf(stderr, "MAPMUT %s stale-exit=%d\n", mode, stale);
  return stale == 113 ? 0 : 20;
}

static int mode_map_fixed(uintptr_t unpacker) {
  void* p = map_exec(Page);
  write_return(p, 70123);
  int old = register_and_call(reinterpret_cast<uintptr_t>(p), unpacker, 1);
  if (old != 70123) return 10;

  void* replaced = mmap(p, Page, PROT_READ | PROT_WRITE | PROT_EXEC,
                        MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
  if (replaced != p) die("mmap MAP_FIXED");
  write_return(p, 70133);

  int stale_rc = expect_stale_113("map-fixed");
  int fresh = register_and_call(reinterpret_cast<uintptr_t>(p), unpacker, 2);
  std::fprintf(stderr, "MAPMUT map-fixed fresh=%d\n", fresh);
  munmap(p, Page);
  if (fresh != 70133) return 11;
  return stale_rc;
}

static int mode_map_fixed_fail(uintptr_t unpacker) {
  void* p = map_exec(Page);
  write_return(p, 70223);
  if (register_and_call(reinterpret_cast<uintptr_t>(p), unpacker, 1) != 70223) return 10;

  errno = 0;
  void* failed = mmap(p, Page, PROT_READ, MAP_PRIVATE | MAP_FIXED, -1, 0);
  int e = errno;
  std::fprintf(stderr, "MAPMUT map-fixed-fail result=%p errno=%d\n", failed, e);
  if (failed != MAP_FAILED || e != EBADF) return 11;

  int live = call_first(2);
  std::fprintf(stderr, "MAPMUT map-fixed-fail live=%d\n", live);
  munmap(p, Page);
  return live == 70223 ? 0 : 12;
}

static int mode_map_noreplace(uintptr_t unpacker) {
  void* p = map_exec(Page);
  write_return(p, 70273);
  if (register_and_call(reinterpret_cast<uintptr_t>(p), unpacker, 1) != 70273) return 10;

  errno = 0;
  void* failed = mmap(p, Page, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
  int e = errno;
  std::fprintf(stderr, "MAPMUT map-noreplace result=%p errno=%d\n", failed, e);
  if (failed != MAP_FAILED || e != EEXIST) return 11;

  int live = call_first(2);
  std::fprintf(stderr, "MAPMUT map-noreplace live=%d\n", live);
  munmap(p, Page);
  return live == 70273 ? 0 : 12;
}

static int mode_mremap_move(uintptr_t unpacker) {
  void* src = map_exec(Page);
  write_return(src, 70323);
  if (register_and_call(reinterpret_cast<uintptr_t>(src), unpacker, 1) != 70323) return 10;

  void* dst = mmap(nullptr, Page, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (dst == MAP_FAILED) die("mmap destination");
  void* moved = mremap(src, Page, Page, MREMAP_MAYMOVE | MREMAP_FIXED, dst);
  if (moved != dst) die("mremap fixed move");

  int stale_rc = expect_stale_113("mremap-move");
  int fresh = register_and_call(reinterpret_cast<uintptr_t>(dst), unpacker, 2);
  std::fprintf(stderr, "MAPMUT mremap-move fresh=%d\n", fresh);
  munmap(dst, Page);
  if (fresh != 70323) return 11;
  return stale_rc;
}

static int mode_mremap_dest(uintptr_t unpacker) {
  void* dst = map_exec(Page);
  write_return(dst, 70423);
  if (register_and_call(reinterpret_cast<uintptr_t>(dst), unpacker, 1) != 70423) return 10;

  void* src = map_exec(Page);
  write_return(src, 70433);
  void* moved = mremap(src, Page, Page, MREMAP_MAYMOVE | MREMAP_FIXED, dst);
  if (moved != dst) die("mremap destination replacement");

  int stale_rc = expect_stale_113("mremap-dest");
  int fresh = register_and_call(reinterpret_cast<uintptr_t>(dst), unpacker, 2);
  std::fprintf(stderr, "MAPMUT mremap-dest fresh=%d\n", fresh);
  munmap(dst, Page);
  if (fresh != 70433) return 11;
  return stale_rc;
}

static int mode_mremap_shrink_tail(uintptr_t unpacker) {
  auto* p = reinterpret_cast<unsigned char*>(map_exec(Page * 2));
  void* tail = p + Page;
  write_return(tail, 70523);
  if (register_and_call(reinterpret_cast<uintptr_t>(tail), unpacker, 1) != 70523) return 10;

  void* shrunk = mremap(p, Page * 2, Page, 0);
  if (shrunk != p) die("mremap shrink tail");
  int stale_rc = expect_stale_113("mremap-shrink-tail");
  munmap(p, Page);
  return stale_rc;
}

static int mode_mremap_shrink_prefix(uintptr_t unpacker) {
  auto* p = reinterpret_cast<unsigned char*>(map_exec(Page * 2));
  write_return(p, 70623);
  if (register_and_call(reinterpret_cast<uintptr_t>(p), unpacker, 1) != 70623) return 10;

  void* shrunk = mremap(p, Page * 2, Page, 0);
  if (shrunk != p) die("mremap shrink prefix");
  int live = call_first(2);
  std::fprintf(stderr, "MAPMUT mremap-shrink-prefix live=%d\n", live);
  munmap(p, Page);
  return live == 70623 ? 0 : 11;
}

static int mode_mremap_fail(uintptr_t unpacker) {
  void* src = map_exec(Page);
  write_return(src, 70723);
  if (register_and_call(reinterpret_cast<uintptr_t>(src), unpacker, 1) != 70723) return 10;
  void* dst = mmap(nullptr, Page, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (dst == MAP_FAILED) die("mmap destination");

  errno = 0;
  void* failed = mremap(src, Page, Page, MREMAP_FIXED, dst); // missing MREMAP_MAYMOVE
  int e = errno;
  std::fprintf(stderr, "MAPMUT mremap-fail result=%p errno=%d\n", failed, e);
  if (failed != MAP_FAILED || e != EINVAL) return 11;
  int live = call_first(2);
  std::fprintf(stderr, "MAPMUT mremap-fail live=%d\n", live);
  munmap(src, Page);
  munmap(dst, Page);
  return live == 70723 ? 0 : 12;
}

static int mode_mremap_dontunmap(uintptr_t unpacker) {
#ifdef MREMAP_DONTUNMAP
  void* src = map_exec(Page);
  write_return(src, 70823);
  if (register_and_call(reinterpret_cast<uintptr_t>(src), unpacker, 1) != 70823) return 10;

  void* moved = mremap(src, Page, Page, MREMAP_MAYMOVE | MREMAP_DONTUNMAP);
  if (moved == MAP_FAILED) die("mremap DONTUNMAP");
  std::fprintf(stderr, "MAPMUT mremap-dontunmap old=%p new=%p\n", src, moved);

  int stale_rc = expect_stale_113("mremap-dontunmap");
  int fresh = register_and_call(reinterpret_cast<uintptr_t>(moved), unpacker, 2);
  std::fprintf(stderr, "MAPMUT mremap-dontunmap fresh=%d\n", fresh);
  munmap(src, Page);
  munmap(moved, Page);
  if (fresh != 70823) return 11;
  return stale_rc;
#else
  std::fprintf(stderr, "MAPMUT mremap-dontunmap unsupported-at-build\n");
  return 77;
#endif
}

static int make_shm() {
  int id = shmget(IPC_PRIVATE, Page, IPC_CREAT | 0700);
  if (id < 0) die("shmget");
  return id;
}

static int mode_shmdt(uintptr_t unpacker) {
  int id = make_shm();
  void* p = shmat(id, nullptr, SHM_EXEC);
  if (p == reinterpret_cast<void*>(-1)) die("shmat SHM_EXEC");
  write_return(p, 70923);
  if (register_and_call(reinterpret_cast<uintptr_t>(p), unpacker, 1) != 70923) return 10;
  if (shmdt(p) != 0) die("shmdt");

  int stale_rc = expect_stale_113("shmdt");
  shmctl(id, IPC_RMID, nullptr);
  return stale_rc;
}

static int mode_shm_remap(uintptr_t unpacker) {
#ifdef SHM_REMAP
  void* dst = map_exec(Page);
  write_return(dst, 71023);
  if (register_and_call(reinterpret_cast<uintptr_t>(dst), unpacker, 1) != 71023) return 10;

  int id = make_shm();
  void* temp = shmat(id, nullptr, SHM_EXEC);
  if (temp == reinterpret_cast<void*>(-1)) die("shmat temp");
  write_return(temp, 71033);
  if (shmdt(temp) != 0) die("shmdt temp");

  void* remapped = shmat(id, dst, SHM_EXEC | SHM_REMAP);
  if (remapped != dst) die("shmat SHM_REMAP");
  int stale_rc = expect_stale_113("shm-remap");
  int fresh = register_and_call(reinterpret_cast<uintptr_t>(dst), unpacker, 2);
  std::fprintf(stderr, "MAPMUT shm-remap fresh=%d\n", fresh);
  shmdt(dst);
  shmctl(id, IPC_RMID, nullptr);
  if (fresh != 71033) return 11;
  return stale_rc;
#else
  std::fprintf(stderr, "MAPMUT shm-remap unsupported-at-build\n");
  return 77;
#endif
}

int main(int argc, char** argv) {
  setvbuf(stderr, nullptr, _IONBF, 0);
  if (argc != 2) {
    std::fprintf(stderr, "usage: %s MODE\n", argv[0]);
    return 64;
  }

  load_host_thunk_library();
  void* h = dlopen("./guest/liblifetime-guest.so", RTLD_NOW | RTLD_LOCAL);
  if (!h) {
    std::fprintf(stderr, "MAPMUT dlopen: %s\n", dlerror());
    return 3;
  }
  auto unpacker_address = sym<GetAddress>(h, "lifetime_guest_unpacker_address");
  const uintptr_t unpacker = unpacker_address();

  int rc = 65;
  if (!std::strcmp(argv[1], "map-fixed")) rc = mode_map_fixed(unpacker);
  else if (!std::strcmp(argv[1], "map-fixed-fail")) rc = mode_map_fixed_fail(unpacker);
  else if (!std::strcmp(argv[1], "map-noreplace")) rc = mode_map_noreplace(unpacker);
  else if (!std::strcmp(argv[1], "mremap-move")) rc = mode_mremap_move(unpacker);
  else if (!std::strcmp(argv[1], "mremap-dest")) rc = mode_mremap_dest(unpacker);
  else if (!std::strcmp(argv[1], "mremap-shrink-tail")) rc = mode_mremap_shrink_tail(unpacker);
  else if (!std::strcmp(argv[1], "mremap-shrink-prefix")) rc = mode_mremap_shrink_prefix(unpacker);
  else if (!std::strcmp(argv[1], "mremap-fail")) rc = mode_mremap_fail(unpacker);
  else if (!std::strcmp(argv[1], "mremap-dontunmap")) rc = mode_mremap_dontunmap(unpacker);
  else if (!std::strcmp(argv[1], "shmdt")) rc = mode_shmdt(unpacker);
  else if (!std::strcmp(argv[1], "shm-remap")) rc = mode_shm_remap(unpacker);
  else std::fprintf(stderr, "MAPMUT unknown mode=%s\n", argv[1]);

  std::fprintf(stderr, "MAPMUT mode=%s rc=%d\n", argv[1], rc);
  dlclose(h);
  return rc;
}
''')

    text = makefile.read_text()
    target = 'guest/callback_mapping_mutation'
    if target not in text:
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith('all:'):
                lines[i] = line + ' ' + target
                break
        else:
            raise SystemExit('Makefile all target missing')

        for i, line in enumerate(lines):
            if line.startswith('\trm -f ') and 'guest/' in line:
                lines[i] = line + ' ' + target
                break

        text = '\n'.join(lines) + '\n'
        text += '''\nguest/callback_mapping_mutation: guest/callback_mapping_mutation.cpp\n\t$(GUEST_CXX) $(GUEST_CXXFLAGS) -pthread -o $@ $< -ldl\n'''
        makefile.write_text(text)

    print('Added callback mapping-mutation lifetime fixture')


if __name__ == '__main__':
    main()
