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
constexpr uintptr_t HMove = 0x0000700000050000ULL;
constexpr uintptr_t HGrow = 0x0000700000051000ULL;
constexpr uintptr_t HShrinkKeep = 0x0000700000052000ULL;
constexpr uintptr_t HShrinkTail = 0x0000700000053000ULL;
using Fn = int (*)();

void Emit(void* p, uint32_t value) {
  auto* c = static_cast<unsigned char*>(p);
  c[0] = 0xB8;
  std::memcpy(&c[1], &value, sizeof(value));
  c[5] = 0xC3;
  __builtin___clear_cache(reinterpret_cast<char*>(p), reinterpret_cast<char*>(p) + 6);
}

bool MakeRX(void* p, size_t len) {
  return ::mprotect(p, len, PROT_READ | PROT_EXEC) == 0;
}

int ForcedMove(bool Reregister) {
  const size_t page = static_cast<size_t>(::sysconf(_SC_PAGESIZE));
  void* reserve = ::mmap(nullptr, 3 * page, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (reserve == MAP_FAILED) return 10;

  void* src = ::mmap(reserve, page, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
  if (src != reserve) return 11;
  void* blocker_addr = static_cast<char*>(reserve) + page;
  void* blocker = ::mmap(blocker_addr, page, PROT_NONE,
                         MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
  if (blocker != blocker_addr) return 12;
  (void)::munmap(static_cast<char*>(reserve) + 2 * page, page);

  Emit(src, 111);
  if (!MakeRX(src, page)) return 13;
  LinkAddressToFunction(HMove, reinterpret_cast<uintptr_t>(src));
  auto linked = reinterpret_cast<Fn>(HMove);
  if (linked() != 111) return 14;
  std::fprintf(stderr, "MREMAP_GENERAL move-warm H=%p src=%p value=111 reregister=%d\n",
               reinterpret_cast<void*>(HMove), src, Reregister ? 1 : 0);

  void* moved = ::mremap(src, page, 2 * page, MREMAP_MAYMOVE);
  if (moved == MAP_FAILED) {
    std::fprintf(stderr, "MREMAP_GENERAL move failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 15;
  }
  if (moved == src) {
    std::fprintf(stderr, "MREMAP_GENERAL move stayed in place src=%p blocker=%p\n", src, blocker);
    return 16;
  }
  auto moved_fn = reinterpret_cast<Fn>(moved);
  const int moved_value = moved_fn();
  std::fprintf(stderr, "MREMAP_GENERAL move-committed old=%p new=%p moved-value=%d reregister=%d\n",
               src, moved, moved_value, Reregister ? 1 : 0);
  if (moved_value != 111) return 17;

  if (Reregister) {
    LinkAddressToFunction(HMove, reinterpret_cast<uintptr_t>(moved));
    std::fprintf(stderr, "MREMAP_GENERAL move-reregister H=%p T=%p\n",
                 reinterpret_cast<void*>(HMove), moved);
  }

  const int value = linked();
  std::fprintf(stderr, "MREMAP_GENERAL move-final H-value=%d reregister=%d\n", value, Reregister ? 1 : 0);
  return value == 111 ? 0 : 18;
}

int InPlaceGrow() {
  const size_t page = static_cast<size_t>(::sysconf(_SC_PAGESIZE));
  void* reserve = ::mmap(nullptr, 2 * page, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (reserve == MAP_FAILED) return 20;
  void* src = ::mmap(reserve, page, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
  if (src != reserve) return 21;
  if (::munmap(static_cast<char*>(reserve) + page, page) != 0) return 22;

  Emit(src, 111);
  if (!MakeRX(src, page)) return 23;
  LinkAddressToFunction(HGrow, reinterpret_cast<uintptr_t>(src));
  auto linked = reinterpret_cast<Fn>(HGrow);
  if (linked() != 111) return 24;

  void* result = ::mremap(src, page, 2 * page, 0);
  if (result == MAP_FAILED) {
    std::fprintf(stderr, "MREMAP_GENERAL grow failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 25;
  }
  const int value = linked();
  std::fprintf(stderr, "MREMAP_GENERAL grow result=%p src=%p H-value=%d same=%d\n",
               result, src, value, result == src ? 1 : 0);
  return result == src && value == 111 ? 0 : 26;
}

int InPlaceShrinkReuse() {
  const size_t page = static_cast<size_t>(::sysconf(_SC_PAGESIZE));
  void* src = ::mmap(nullptr, 2 * page, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (src == MAP_FAILED) return 30;
  void* tail = static_cast<char*>(src) + page;
  Emit(src, 111);
  Emit(tail, 222);
  if (!MakeRX(src, 2 * page)) return 31;

  LinkAddressToFunction(HShrinkKeep, reinterpret_cast<uintptr_t>(src));
  LinkAddressToFunction(HShrinkTail, reinterpret_cast<uintptr_t>(tail));
  auto keep = reinterpret_cast<Fn>(HShrinkKeep);
  auto old_tail = reinterpret_cast<Fn>(HShrinkTail);
  if (keep() != 111 || old_tail() != 222) return 32;
  std::fprintf(stderr, "MREMAP_GENERAL shrink-warm keep=%p tail=%p values=111,222\n", src, tail);

  void* result = ::mremap(src, 2 * page, page, 0);
  if (result == MAP_FAILED || result != src) {
    std::fprintf(stderr, "MREMAP_GENERAL shrink failed result=%p errno=%d (%s)\n",
                 result, errno, std::strerror(errno));
    return 33;
  }

  void* reused = ::mmap(tail, page, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
  if (reused == MAP_FAILED || reused != tail) {
    std::fprintf(stderr, "MREMAP_GENERAL tail-reuse mmap failed result=%p errno=%d (%s)\n",
                 reused, errno, std::strerror(errno));
    return 34;
  }
  Emit(reused, 333);
  if (!MakeRX(reused, page)) return 35;

  const int keep_value = keep();
  std::fprintf(stderr, "MREMAP_GENERAL shrink-keep value=%d reused=%p\n", keep_value, reused);
  const int tail_value = old_tail();
  std::fprintf(stderr,
               "MREMAP_GENERAL shrink-final keep-value=%d tail-value=%d reused=%p expected-current-gap=333\n",
               keep_value, tail_value, reused);
  return keep_value == 111 && tail_value == 333 ? 0 : 36;
}
} // namespace

int main(int argc, char** argv) {
  setvbuf(stderr, nullptr, _IONBF, 0);
  if (argc != 2) return 64;
  const long ps = ::sysconf(_SC_PAGESIZE);
  if (ps <= 0) return 2;

  if (std::strcmp(argv[1], "--move") == 0) return ForcedMove(false);
  if (std::strcmp(argv[1], "--move-reregister") == 0) return ForcedMove(true);
  if (std::strcmp(argv[1], "--grow") == 0) return InPlaceGrow();
  if (std::strcmp(argv[1], "--shrink-reuse") == 0) return InPlaceShrinkReuse();
  return 64;
}
