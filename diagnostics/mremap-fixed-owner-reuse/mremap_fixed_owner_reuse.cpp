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
  if (dst == MAP_FAILED || src == MAP_FAILED) {
    std::fprintf(stderr, "MREMAP_REUSE setup failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 3;
  }

  LinkAddressToFunction(H, reinterpret_cast<uintptr_t>(dst));
  auto linked = reinterpret_cast<Fn>(H);
  const int warm = linked();
  std::fprintf(stderr, "MREMAP_REUSE warm H=%p dst=%p src=%p value=%d\n", reinterpret_cast<void*>(H), dst, src, warm);
  if (warm != 111) return 4;

  void* moved = ::mremap(src, page, page, MREMAP_MAYMOVE | MREMAP_FIXED, dst);
  if (moved == MAP_FAILED || moved != dst) {
    std::fprintf(stderr, "MREMAP_REUSE mremap failed result=%p errno=%d (%s)\n", moved, errno, std::strerror(errno));
    return 5;
  }
  std::fprintf(stderr, "MREMAP_REUSE committed H=%p T=%p source-owner-moved sentinel=222 reregister=%d\n",
               reinterpret_cast<void*>(H), dst, Reregister ? 1 : 0);

  if (Reregister) {
    LinkAddressToFunction(H, reinterpret_cast<uintptr_t>(dst));
  }

  const int value = linked();
  std::fprintf(stderr, "MREMAP_REUSE final value=%d reregister=%d\n", value, Reregister ? 1 : 0);
  return value == 222 ? 0 : 6;
}
