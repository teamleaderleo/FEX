#define _GNU_SOURCE
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wmissing-field-initializers"
#include "common/Guest.h"
#pragma GCC diagnostic pop

#include <array>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <sys/mman.h>
#include <unistd.h>

namespace {
constexpr uintptr_t H = 0x0000700000040000ULL;
using Fn = int (*)();

void Emit(void* p, uint32_t value) {
  auto* c = static_cast<unsigned char*>(p);
  c[0] = 0xB8;
  std::memcpy(&c[1], &value, sizeof(value));
  c[5] = 0xC3;
  __builtin___clear_cache(reinterpret_cast<char*>(p), reinterpret_cast<char*>(p) + 6);
}

void PrintBytes(const char* label, const std::array<unsigned char, 6>& bytes) {
  std::fprintf(stderr, "DONTUNMAP %s=%02x%02x%02x%02x%02x%02x\n",
               label, bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5]);
}
} // namespace

int main(int argc, char** argv) {
  setvbuf(stderr, nullptr, _IONBF, 0);
  bool Reregister = false;
  bool InspectOnly = false;
  if (argc == 2 && std::strcmp(argv[1], "--reregister") == 0) {
    Reregister = true;
  } else if (argc == 2 && std::strcmp(argv[1], "--inspect-only") == 0) {
    InspectOnly = true;
  } else if (argc != 1) {
    return 64;
  }

  const long ps = ::sysconf(_SC_PAGESIZE);
  if (ps <= 0) return 2;
  const size_t page = static_cast<size_t>(ps);

  void* src = ::mmap(nullptr, page, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (src == MAP_FAILED) {
    std::fprintf(stderr, "DONTUNMAP setup mmap failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 3;
  }
  Emit(src, 111);
  if (::mprotect(src, page, PROT_READ | PROT_EXEC) != 0) {
    std::fprintf(stderr, "DONTUNMAP setup mprotect failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 4;
  }

  LinkAddressToFunction(H, reinterpret_cast<uintptr_t>(src));
  auto linked = reinterpret_cast<Fn>(H);
  const int warm = linked();
  std::fprintf(stderr, "DONTUNMAP warm H=%p src=%p value=%d\n", reinterpret_cast<void*>(H), src, warm);
  if (warm != 111) return 5;

  void* moved = ::mremap(src, page, page, MREMAP_MAYMOVE | MREMAP_DONTUNMAP);
  if (moved == MAP_FAILED) {
    std::fprintf(stderr, "DONTUNMAP mremap failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 6;
  }
  if (moved == src) {
    std::fprintf(stderr, "DONTUNMAP unexpectedly reused source VA=%p\n", src);
    return 7;
  }

  std::array<unsigned char, 6> old_bytes {};
  std::array<unsigned char, 6> new_bytes {};
  std::memcpy(old_bytes.data(), src, old_bytes.size());
  std::memcpy(new_bytes.data(), moved, new_bytes.size());
  PrintBytes("old-bytes", old_bytes);
  PrintBytes("new-bytes", new_bytes);

  auto moved_fn = reinterpret_cast<Fn>(moved);
  const int moved_value = moved_fn();
  std::fprintf(stderr,
               "DONTUNMAP moved src=%p new=%p moved-value=%d reregister=%d inspect=%d\n",
               src, moved, moved_value, Reregister ? 1 : 0, InspectOnly ? 1 : 0);

  const bool OldZero = old_bytes == std::array<unsigned char, 6> {};
  const bool NewCode = new_bytes[0] == 0xB8 && new_bytes[5] == 0xC3;
  if (InspectOnly) {
    std::fprintf(stderr, "DONTUNMAP inspect old-zero=%d new-code=%d moved-value=%d\n",
                 OldZero ? 1 : 0, NewCode ? 1 : 0, moved_value);
    return OldZero && NewCode && moved_value == 111 ? 0 : 8;
  }

  if (Reregister) {
    LinkAddressToFunction(H, reinterpret_cast<uintptr_t>(moved));
    std::fprintf(stderr, "DONTUNMAP reregister H=%p T=%p\n", reinterpret_cast<void*>(H), moved);
  }

  const int value = linked();
  std::fprintf(stderr, "DONTUNMAP final H-value=%d reregister=%d old-zero=%d moved-value=%d\n",
               value, Reregister ? 1 : 0, OldZero ? 1 : 0, moved_value);
  return value == 111 ? 0 : 9;
}
