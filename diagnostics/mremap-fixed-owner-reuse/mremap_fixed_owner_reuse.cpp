#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wmissing-field-initializers"
#include "common/Guest.h"
#pragma GCC diagnostic pop

#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <sys/mman.h>
#include <unistd.h>

namespace {
constexpr uintptr_t H = 0x0000700000030000ULL;
using Fn = int (*)();

void Emit(void* p, uint32_t v) {
  auto* c = static_cast<unsigned char*>(p);
  c[0] = 0xB8;
  std::memcpy(&c[1], &v, sizeof(v));
  c[5] = 0xC3;
  __builtin___clear_cache(reinterpret_cast<char*>(p), reinterpret_cast<char*>(p) + 6);
}

void* MakeCode(size_t page, uint32_t v) {
  void* p = ::mmap(nullptr, page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (p == MAP_FAILED) return p;
  Emit(p, v);
  if (::mprotect(p, page, PROT_READ | PROT_EXEC) != 0) return MAP_FAILED;
  return p;
}
}

int main(int argc, char** argv) {
  setvbuf(stderr, nullptr, _IONBF, 0);
  const bool Reregister = argc == 2 && std::strcmp(argv[1], "--reregister") == 0;
  const bool FailMove = argc == 2 && std::strcmp(argv[1], "--fail") == 0;
  if (argc > 2 || (argc == 2 && !Reregister && !FailMove)) return 64;

  const long ps = ::sysconf(_SC_PAGESIZE);
  if (ps <= 0) return 2;
  const size_t page = static_cast<size_t>(ps);

  void* dst = MakeCode(page, 111);
  void* src = MakeCode(page, 222);
  if (dst == MAP_FAILED || src == MAP_FAILED) return 3;

  LinkAddressToFunction(H, reinterpret_cast<uintptr_t>(dst));
  auto linked = reinterpret_cast<Fn>(H);
  auto direct = reinterpret_cast<Fn>(dst);
  const int warm = linked();
  std::fprintf(stderr, "MREMAP_REUSE warm H=%p dst=%p src=%p value=%d mode=%s\n",
               reinterpret_cast<void*>(H), dst, src, warm,
               FailMove ? "fail" : (Reregister ? "reregister" : "no-reregister"));
  if (warm != 111) return 4;

  const int MoveFlags = FailMove ? MREMAP_FIXED : (MREMAP_MAYMOVE | MREMAP_FIXED);
  errno = 0;
  void* moved = ::mremap(src, page, page, MoveFlags, dst);

  if (FailMove) {
    if (moved != MAP_FAILED) {
      std::fprintf(stderr, "MREMAP_REUSE fail-control unexpectedly succeeded result=%p\n", moved);
      return 5;
    }
    const int SavedErrno = errno;
    const int direct_value = direct();
    const int h_value = linked();
    std::fprintf(stderr,
                 "MREMAP_REUSE rollback errno=%d (%s) direct=%d H-value=%d\n",
                 SavedErrno, std::strerror(SavedErrno), direct_value, h_value);
    return SavedErrno == EINVAL && direct_value == 111 && h_value == 111 ? 0 : 6;
  }

  if (moved == MAP_FAILED || moved != dst) return 7;
  std::fprintf(stderr, "MREMAP_REUSE committed H=%p T=%p source-owner-moved sentinel=222 reregister=%d\n",
               reinterpret_cast<void*>(H), dst, Reregister ? 1 : 0);

  const int direct_before = direct();
  std::fprintf(stderr, "MREMAP_REUSE direct-before-invalidate value=%d\n", direct_before);

  // The discovery carrier uses this permission transition to expose stale
  // destination translation. A repaired MREMAP_FIXED path should already have
  // invalidated T, so both direct calls return 222.
  if (::mprotect(dst, page, PROT_READ | PROT_WRITE) != 0) return 8;
  if (::mprotect(dst, page, PROT_READ | PROT_EXEC) != 0) return 9;
  const int direct_after = direct();
  std::fprintf(stderr, "MREMAP_REUSE direct-after-invalidate value=%d\n", direct_after);

  if (Reregister) {
    LinkAddressToFunction(H, reinterpret_cast<uintptr_t>(dst));
    std::fprintf(stderr, "MREMAP_REUSE reregistered H=%p T=%p\n", reinterpret_cast<void*>(H), dst);
  }

  const int value = linked();
  std::fprintf(stderr, "MREMAP_REUSE final H-value=%d reregister=%d direct-before=%d direct-after=%d\n",
               value, Reregister ? 1 : 0, direct_before, direct_after);

  return direct_before == 222 && direct_after == 222 && value == 222 ? 0 : 10;
}
