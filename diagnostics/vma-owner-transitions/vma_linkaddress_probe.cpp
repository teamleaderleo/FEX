#include "common/Guest.h"

#include <cerrno>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

namespace {
constexpr uintptr_t SyntheticH = 0x0000700000020000ULL;

using Fn = int (*)();

void EmitReturn(void* Page, uint32_t Value) {
  auto* Code = static_cast<unsigned char*>(Page);
  // mov eax, imm32 ; ret
  Code[0] = 0xB8;
  std::memcpy(&Code[1], &Value, sizeof(Value));
  Code[5] = 0xC3;
  __builtin___clear_cache(reinterpret_cast<char*>(Page), reinterpret_cast<char*>(Page) + 6);
}

bool Protect(void* Page, size_t Size, int Prot) {
  if (::mprotect(Page, Size, Prot) != 0) {
    std::fprintf(stderr, "VMA mprotect failed errno=%d (%s)\n", errno, std::strerror(errno));
    return false;
  }
  return true;
}

int ChildCall(Fn Function) {
  const pid_t Child = ::fork();
  if (Child < 0) {
    std::fprintf(stderr, "VMA fork failed errno=%d (%s)\n", errno, std::strerror(errno));
    return -1;
  }
  if (Child == 0) {
    const int Value = Function();
    std::fprintf(stderr, "VMA child-return value=%d\n", Value);
    _exit(Value == 111 ? 11 : Value == 222 ? 22 : Value == 333 ? 33 : 99);
  }

  int Status {};
  if (::waitpid(Child, &Status, 0) != Child) {
    std::fprintf(stderr, "VMA waitpid failed errno=%d (%s)\n", errno, std::strerror(errno));
    return -1;
  }
  if (WIFSIGNALED(Status)) {
    std::fprintf(stderr, "VMA child-signal=%d (%s)\n", WTERMSIG(Status), strsignal(WTERMSIG(Status)));
    return 128 + WTERMSIG(Status);
  }
  if (WIFEXITED(Status)) {
    std::fprintf(stderr, "VMA child-exit=%d\n", WEXITSTATUS(Status));
    return WEXITSTATUS(Status);
  }
  std::fprintf(stderr, "VMA child-unexpected-status=0x%x\n", Status);
  return -1;
}
} // namespace

int main(int argc, char** argv) {
  setvbuf(stderr, nullptr, _IONBF, 0);
  const bool MapFixed = argc == 2 && std::strcmp(argv[1], "map-fixed") == 0;
  const bool MapFixedReregister = argc == 2 && std::strcmp(argv[1], "map-fixed-reregister") == 0;
  const bool Mprotect = argc == 2 && std::strcmp(argv[1], "mprotect") == 0;
  if (!MapFixed && !MapFixedReregister && !Mprotect) {
    std::fprintf(stderr, "usage: %s map-fixed|map-fixed-reregister|mprotect\n", argv[0]);
    return 64;
  }

  const long PageSizeLong = ::sysconf(_SC_PAGESIZE);
  if (PageSizeLong <= 0) {
    return 2;
  }
  const size_t PageSize = static_cast<size_t>(PageSizeLong);

  void* Target = ::mmap(nullptr, PageSize, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (Target == MAP_FAILED) {
    std::fprintf(stderr, "VMA initial mmap failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 3;
  }
  EmitReturn(Target, 111);
  if (!Protect(Target, PageSize, PROT_READ | PROT_EXEC)) {
    return 4;
  }

  LinkAddressToFunction(SyntheticH, reinterpret_cast<uintptr_t>(Target));
  auto Linked = reinterpret_cast<Fn>(SyntheticH);

  const int First = Linked();
  std::fprintf(stderr, "VMA first H=%p T=%p value=%d\n", reinterpret_cast<void*>(SyntheticH), Target, First);
  if (First != 111) {
    return 5;
  }

  if (MapFixed || MapFixedReregister) {
    void* Replacement = ::mmap(Target, PageSize, PROT_READ | PROT_WRITE,
                               MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
    if (Replacement == MAP_FAILED || Replacement != Target) {
      std::fprintf(stderr, "VMA MAP_FIXED failed result=%p errno=%d (%s)\n", Replacement, errno, std::strerror(errno));
      return 6;
    }
    EmitReturn(Target, 222);
    if (!Protect(Target, PageSize, PROT_READ | PROT_EXEC)) {
      return 7;
    }

    std::fprintf(stderr, "VMA replaced-same-address H=%p T=%p generation=2 sentinel=222\n",
                 reinterpret_cast<void*>(SyntheticH), Target);

    if (MapFixedReregister) {
      std::fprintf(stderr, "VMA explicit-reregister H=%p T=%p generation=2\n",
                   reinterpret_cast<void*>(SyntheticH), Target);
      LinkAddressToFunction(SyntheticH, reinterpret_cast<uintptr_t>(Target));
    }

    const int Second = Linked();
    std::fprintf(stderr, "VMA after-map-fixed value=%d reregister=%d\n", Second, MapFixedReregister ? 1 : 0);
    return Second == 222 ? 0 : 8;
  }

  if (!Protect(Target, PageSize, PROT_NONE)) {
    return 9;
  }
  std::fprintf(stderr, "VMA execute-permission-removed H=%p T=%p\n",
               reinterpret_cast<void*>(SyntheticH), Target);
  const int DeadCall = ChildCall(Linked);
  std::fprintf(stderr, "VMA dead-call-status=%d\n", DeadCall);

  if (!Protect(Target, PageSize, PROT_READ | PROT_WRITE)) {
    return 10;
  }
  EmitReturn(Target, 333);
  if (!Protect(Target, PageSize, PROT_READ | PROT_EXEC)) {
    return 11;
  }
  std::fprintf(stderr, "VMA execute-permission-restored-with-new-code H=%p T=%p sentinel=333\n",
               reinterpret_cast<void*>(SyntheticH), Target);
  const int Restored = Linked();
  std::fprintf(stderr, "VMA after-mprotect-reuse value=%d\n", Restored);

  return (DeadCall >= 128 && Restored == 333) ? 0 : 12;
}
