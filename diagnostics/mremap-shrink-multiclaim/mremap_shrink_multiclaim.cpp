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
constexpr uintptr_t H = 0x0000700000070000ULL;
using Fn = int (*)();

void Emit(void* p, uint32_t value) {
  auto* c = static_cast<unsigned char*>(p);
  c[0] = 0xB8;
  std::memcpy(&c[1], &value, sizeof(value));
  c[5] = 0xC3;
  __builtin___clear_cache(reinterpret_cast<char*>(p), reinterpret_cast<char*>(p) + 6);
}
}

int main() {
  setvbuf(stderr, nullptr, _IONBF, 0);
  const long ps = ::sysconf(_SC_PAGESIZE);
  if (ps <= 0) return 2;
  const size_t page = static_cast<size_t>(ps);

  void* base = ::mmap(nullptr, 2 * page, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (base == MAP_FAILED) return 3;
  void* tail = static_cast<char*>(base) + page;
  Emit(base, 111);
  Emit(tail, 222);
  if (::mprotect(base, 2 * page, PROT_READ | PROT_EXEC) != 0) return 4;

  // One synthetic host key owns both pieces. A is active; B is standby.
  LinkAddressToFunction(H, reinterpret_cast<uintptr_t>(base));
  LinkAddressToFunction(H, reinterpret_cast<uintptr_t>(tail));
  auto linked = reinterpret_cast<Fn>(H);
  const int warm = linked();
  std::fprintf(stderr, "SHRINK_MULTI warm H=%p A=%p B=%p value=%d\n",
               reinterpret_cast<void*>(H), base, tail, warm);
  if (warm != 111) return 5;

  // MAYMOVE is present, so a general transaction has to prepare both pieces.
  // Shrinking should stay in place on Linux; the retained-prefix snapshot must
  // roll back without resurrecting the already-retired tail claim.
  void* result = ::mremap(base, 2 * page, page, MREMAP_MAYMOVE);
  if (result == MAP_FAILED || result != base) {
    std::fprintf(stderr, "SHRINK_MULTI shrink result=%p base=%p errno=%d (%s)\n",
                 result, base, errno, std::strerror(errno));
    return 6;
  }

  void* reused = ::mmap(tail, page, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
  if (reused == MAP_FAILED || reused != tail) {
    std::fprintf(stderr, "SHRINK_MULTI tail reuse failed result=%p errno=%d (%s)\n",
                 reused, errno, std::strerror(errno));
    return 7;
  }
  Emit(reused, 333);
  if (::mprotect(reused, page, PROT_READ | PROT_EXEC) != 0) return 8;

  const int retained = linked();
  std::fprintf(stderr, "SHRINK_MULTI after-shrink H-value=%d reused-tail=%p\n", retained, reused);
  if (retained != 111) return 9;

  // Remove the sole legitimate A claim. Correct tail-first transaction state
  // has no B left, so H becomes revoked. A bad prefix-first rollback resurrects
  // B and promotes it here, sending H into unrelated 333 code at the reused VA.
  if (::munmap(base, page) != 0) {
    std::fprintf(stderr, "SHRINK_MULTI unmap-A failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 10;
  }
  std::fprintf(stderr, "SHRINK_MULTI removed-A H=%p A=%p old-B=%p replacement=333\n",
               reinterpret_cast<void*>(H), base, tail);

  const int after_remove = linked();
  std::fprintf(stderr, "SHRINK_MULTI after-remove-A H-value=%d bad-resurrection=333\n", after_remove);
  return after_remove == 333 ? 0 : 11;
}
