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
constexpr const char* TargetPath = "/tmp/fex-thunk-inflight-target";
constexpr const char* SelectedPath = "/tmp/fex-thunk-inflight-selected";
constexpr const char* ResumePath = "/tmp/fex-thunk-inflight-resume";

using Fn = int (*)();
std::atomic<int> WorkerValue {-1};

void EmitReturn(void* Page, uint32_t Value) {
  auto* Code = static_cast<unsigned char*>(Page);
  Code[0] = 0xB8;
  std::memcpy(&Code[1], &Value, sizeof(Value));
  Code[5] = 0xC3;
  __builtin___clear_cache(reinterpret_cast<char*>(Page), reinterpret_cast<char*>(Page) + 6);
}

bool WriteMarker(const char* Path, const char* Data, size_t Length) {
  const int FD = ::open(Path, O_CREAT | O_WRONLY | O_TRUNC, 0600);
  if (FD < 0) {
    std::fprintf(stderr, "INFLIGHT open %s failed errno=%d (%s)\n", Path, errno, std::strerror(errno));
    return false;
  }
  const ssize_t Written = ::write(FD, Data, Length);
  const int SavedErrno = errno;
  (void)::close(FD);
  if (Written != static_cast<ssize_t>(Length)) {
    std::fprintf(stderr, "INFLIGHT write %s failed written=%zd errno=%d (%s)\n",
                 Path, Written, SavedErrno, std::strerror(SavedErrno));
    return false;
  }
  return true;
}

bool Touch(const char* Path) {
  static constexpr char Marker[] = "go\n";
  return WriteMarker(Path, Marker, sizeof(Marker) - 1);
}

bool PublishTarget(uintptr_t Target) {
  char Buffer[32] {};
  const int Length = std::snprintf(Buffer, sizeof(Buffer), "%lx\n", Target);
  if (Length <= 0 || static_cast<size_t>(Length) >= sizeof(Buffer)) {
    return false;
  }
  return WriteMarker(TargetPath, Buffer, static_cast<size_t>(Length));
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

int main(int argc, char** argv) {
  setvbuf(stderr, nullptr, _IONBF, 0);
  const bool Reregister = argc == 2 && std::strcmp(argv[1], "--reregister") == 0;
  if (argc > 2 || (argc == 2 && !Reregister)) {
    return 64;
  }

  (void)::unlink(ArmPath);
  (void)::unlink(TargetPath);
  (void)::unlink(SelectedPath);
  (void)::unlink(ResumePath);

  const long PageSizeLong = ::sysconf(_SC_PAGESIZE);
  if (PageSizeLong <= 0) return 2;
  const size_t PageSize = static_cast<size_t>(PageSizeLong);

  void* Target = ::mmap(nullptr, PageSize, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (Target == MAP_FAILED) return 3;
  EmitReturn(Target, 111);
  if (::mprotect(Target, PageSize, PROT_READ | PROT_EXEC) != 0) return 4;

  LinkAddressToFunction(SyntheticH, reinterpret_cast<uintptr_t>(Target));
  auto Linked = reinterpret_cast<Fn>(SyntheticH);
  const int Warm = Linked();
  std::fprintf(stderr, "INFLIGHT warm H=%p T=%p value=%d reregister=%d\n",
               reinterpret_cast<void*>(SyntheticH), Target, Warm, Reregister ? 1 : 0);
  if (Warm != 111) return 5;

  // Break the warmed direct H->T link while preserving generation 1.
  if (::mprotect(Target, PageSize, PROT_READ | PROT_WRITE) != 0) return 6;
  EmitReturn(Target, 111);
  if (::mprotect(Target, PageSize, PROT_READ | PROT_EXEC) != 0) return 7;
  std::fprintf(stderr, "INFLIGHT relink-reset H=%p T=%p sentinel=111 owner-preserved\n",
               reinterpret_cast<void*>(SyntheticH), Target);

  if (!PublishTarget(reinterpret_cast<uintptr_t>(Target))) return 8;
  if (!Touch(ArmPath)) return 9;
  std::fprintf(stderr, "INFLIGHT armed H=%p T=%p stage=before-target-selection\n",
               reinterpret_cast<void*>(SyntheticH), Target);

  pthread_t Worker {};
  if (::pthread_create(&Worker, nullptr, WorkerMain, nullptr) != 0) return 10;
  if (!WaitFor(SelectedPath)) return 11;
  std::fprintf(stderr, "INFLIGHT old-H-redirect-pending H=%p T=%p\n",
               reinterpret_cast<void*>(SyntheticH), Target);

  void* Replacement = ::mmap(Target, PageSize, PROT_READ | PROT_WRITE,
                             MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
  if (Replacement == MAP_FAILED || Replacement != Target) return 12;
  EmitReturn(Target, 222);
  if (::mprotect(Target, PageSize, PROT_READ | PROT_EXEC) != 0) return 13;
  std::fprintf(stderr, "INFLIGHT replacement-committed H=%p T=%p generation=2 sentinel=222 reregister=%d\n",
               reinterpret_cast<void*>(SyntheticH), Target, Reregister ? 1 : 0);

  if (Reregister) {
    LinkAddressToFunction(SyntheticH, reinterpret_cast<uintptr_t>(Target));
    std::fprintf(stderr, "INFLIGHT reregistered H=%p T=%p generation=2\n",
                 reinterpret_cast<void*>(SyntheticH), Target);
  }

  if (!Touch(ResumePath)) return 14;
  if (::pthread_join(Worker, nullptr) != 0) return 15;

  const int Result = WorkerValue.load(std::memory_order_acquire);
  std::fprintf(stderr, "INFLIGHT final worker-value=%d reregister=%d\n", Result, Reregister ? 1 : 0);

  (void)::unlink(ArmPath);
  (void)::unlink(TargetPath);
  (void)::unlink(SelectedPath);
  (void)::unlink(ResumePath);
  return Result == 222 ? 0 : 16;
}
