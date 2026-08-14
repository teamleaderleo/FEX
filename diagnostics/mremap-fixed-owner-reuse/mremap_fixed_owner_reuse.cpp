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
  if (argc > 2 || (argc == 2 && !Reregister)) return 64;
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
  std::fprintf(stderr, "MREMAP_REUSE warm H=%p dst=%p src=%p value=%d\n", reinterpret_cast<void*>(H), dst, src, warm);
  if (warm != 111) return 4;

  void* moved = ::mremap(src, page, page, MREMAP_MAYMOVE | MREMAP_FIXED, dst);
  if (moved == MAP_FAILED || moved != dst) return 5;
  std::fprintf(stderr, "MREMAP_REUSE committed H=%p T=%p source-owner-moved sentinel=222 reregister=%d\n",
               reinterpret_cast<void*>(H), dst, Reregister ? 1 : 0);

  const int direct_before = direct();
  std::fprintf(stderr, "MREMAP_REUSE direct-before-invalidate value=%d\n", direct_before);

  // Permission-only mutation preserves the moved source owner while forcing
  // FEX code invalidation for the destination VA. This separates mremap's
  // destination code-cache behavior from stale H ownership.
  if (::mprotect(dst, page, PROT_READ | PROT_WRITE) != 0) return 6;
  if (::mprotect(dst, page, PROT_READ | PROT_EXEC) != 0) return 7;
  const int direct_after = direct();
  std::fprintf(stderr, "MREMAP_REUSE direct-after-invalidate value=%d\n", direct_after);

  if (Reregister) {
    LinkAddressToFunction(H, reinterpret_cast<uintptr_t>(dst));
  }

  const int value = linked();
  std::fprintf(stderr, "MREMAP_REUSE final H-value=%d reregister=%d direct-before=%d direct-after=%d\n",
               value, Reregister ? 1 : 0, direct_before, direct_after);

  // Discovery expects stale compiled destination code before explicit
  // invalidation, then generation-2 bytes afterward. H reaching 222 without a
  // new registration exposes the separate destination-owner retirement gap.
  return direct_before == 111 && direct_after == 222 && value == 222 ? 0 : 8;
}
