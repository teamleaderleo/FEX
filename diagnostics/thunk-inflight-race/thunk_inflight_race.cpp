#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wmissing-field-initializers"
#include "common/Guest.h"
#pragma GCC diagnostic pop

#include <atomic>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <pthread.h>
#include <sys/mman.h>
#include <unistd.h>

namespace {
constexpr uintptr_t SyntheticH = 0x0000700000020000ULL;
constexpr const char* ArmPath = "/tmp/fex-thunk-inflight-arm";
constexpr const char* SelectedPath = "/tmp/fex-thunk-inflight-selected";
constexpr const char* ResumePath = "/tmp/fex-thunk-inflight-resume";

using Fn = int (*)();
std::atomic<int> WorkerValue {-1};

void EmitReturn(void* Page, uint32_t Value) {
  auto* Code = static_cast<unsigned char*>(Page);
  // mov eax, imm32 ; ret
  Code[0] = 0xB8;
  std::memcpy(&Code[1], &Value, sizeof(Value));
  Code[5] = 0xC3;
  __builtin___clear_cache(reinterpret_cast<char*>(Page), reinterpret_cast<char*>(Page) + 6);
}

bool Touch(const char* Path) {
  const int FD = ::open(Path, O_CREAT | O_WRONLY | O_TRUNC, 0600);
  if (FD < 0) {
    std::fprintf(stderr, "INFLIGHT touch %s failed errno=%d (%s)\n", Path, errno, std::strerror(errno));
    return false;
  }
  static constexpr char Marker[] = "go\n";
  const ssize_t Written = ::write(FD, Marker, sizeof(Marker) - 1);
  const int SavedErrno = errno;
  (void)::close(FD);
  if (Written != static_cast<ssize_t>(sizeof(Marker) - 1)) {
    std::fprintf(stderr, "INFLIGHT write %s failed written=%zd errno=%d (%s)\n",
                 Path, Written, SavedErrno, std::strerror(SavedErrno));
    return false;
  }
  return true;
}

bool WaitFor(const char* Path) {
  for (size_t I = 0; I < 30000; ++I) {
    if (::access(Path, F_OK) == 0) {
      return true;
    }
    ::usleep(1000);
  }
  std::fprintf(stderr, "INFLIGHT timeout waiting for %s\n", Path);
  return false;
}

void* WorkerMain(void*) {
  auto Linked = reinterpret_cast<Fn>(SyntheticH);
  const int Value = Linked();
  WorkerValue.store(Value, std::memory_order_release);
  std::fprintf(stderr, "INFLIGHT worker-return value=%d\n", Value);
  return nullptr;
}
} // namespace

int main() {
  setvbuf(stderr, nullptr, _IONBF, 0);
  (void)::unlink(ArmPath);
  (void)::unlink(SelectedPath);
  (void)::unlink(ResumePath);

  const long PageSizeLong = ::sysconf(_SC_PAGESIZE);
  if (PageSizeLong <= 0) {
    return 2;
  }
  const size_t PageSize = static_cast<size_t>(PageSizeLong);

  void* Target = ::mmap(nullptr, PageSize, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (Target == MAP_FAILED) {
    std::fprintf(stderr, "INFLIGHT initial mmap failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 3;
  }
  EmitReturn(Target, 111);
  if (::mprotect(Target, PageSize, PROT_READ | PROT_EXEC) != 0) {
    std::fprintf(stderr, "INFLIGHT initial mprotect failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 4;
  }

  LinkAddressToFunction(SyntheticH, reinterpret_cast<uintptr_t>(Target));
  auto Linked = reinterpret_cast<Fn>(SyntheticH);
  const int Warm = Linked();
  std::fprintf(stderr, "INFLIGHT warm H=%p T=%p value=%d\n",
               reinterpret_cast<void*>(SyntheticH), Target, Warm);
  if (Warm != 111) {
    return 5;
  }

  if (!Touch(ArmPath)) {
    return 6;
  }

  pthread_t Worker {};
  if (::pthread_create(&Worker, nullptr, WorkerMain, nullptr) != 0) {
    std::fprintf(stderr, "INFLIGHT pthread_create failed\n");
    return 7;
  }

  if (!WaitFor(SelectedPath)) {
    return 8;
  }
  std::fprintf(stderr, "INFLIGHT selected-before-retire H=%p T=%p\n",
               reinterpret_cast<void*>(SyntheticH), Target);

  void* Replacement = ::mmap(Target, PageSize, PROT_READ | PROT_WRITE,
                             MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
  if (Replacement == MAP_FAILED || Replacement != Target) {
    std::fprintf(stderr, "INFLIGHT MAP_FIXED failed result=%p errno=%d (%s)\n",
                 Replacement, errno, std::strerror(errno));
    return 9;
  }
  EmitReturn(Target, 222);
  if (::mprotect(Target, PageSize, PROT_READ | PROT_EXEC) != 0) {
    std::fprintf(stderr, "INFLIGHT replacement mprotect failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 10;
  }
  std::fprintf(stderr, "INFLIGHT replacement-committed H=%p T=%p generation=2 sentinel=222\n",
               reinterpret_cast<void*>(SyntheticH), Target);

  if (!Touch(ResumePath)) {
    return 11;
  }
  if (::pthread_join(Worker, nullptr) != 0) {
    std::fprintf(stderr, "INFLIGHT pthread_join failed\n");
    return 12;
  }

  const int Result = WorkerValue.load(std::memory_order_acquire);
  std::fprintf(stderr, "INFLIGHT final worker-value=%d reregister=0\n", Result);

  (void)::unlink(ArmPath);
  (void)::unlink(SelectedPath);
  (void)::unlink(ResumePath);

  // This diagnostic lane is expected to expose the current in-flight gap:
  // the worker selected old compiled H before retirement, then executes it
  // after T has become a new owner generation and reaches sentinel 222.
  return Result == 222 ? 0 : 13;
}
