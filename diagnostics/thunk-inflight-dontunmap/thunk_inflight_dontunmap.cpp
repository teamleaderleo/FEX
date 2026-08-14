#define _GNU_SOURCE
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
  if (FD < 0) return false;
  const ssize_t Written = ::write(FD, Data, Length);
  (void)::close(FD);
  return Written == static_cast<ssize_t>(Length);
}

bool Touch(const char* Path) {
  static constexpr char Marker[] = "go\n";
  return WriteMarker(Path, Marker, sizeof(Marker) - 1);
}

bool PublishTarget(uintptr_t Target) {
  char Buffer[32] {};
  const int Length = std::snprintf(Buffer, sizeof(Buffer), "%lx\n", Target);
  return Length > 0 && static_cast<size_t>(Length) < sizeof(Buffer) &&
         WriteMarker(TargetPath, Buffer, static_cast<size_t>(Length));
}

bool WaitFor(const char* Path) {
  for (size_t I = 0; I < 30000; ++I) {
    if (::access(Path, F_OK) == 0) return true;
    ::usleep(1000);
  }
  return false;
}

void* WorkerMain(void*) {
  auto Linked = reinterpret_cast<Fn>(SyntheticH);
  const int Value = Linked();
  WorkerValue.store(Value, std::memory_order_release);
  std::fprintf(stderr, "DONTUNMAP_INFLIGHT worker-return value=%d\n", Value);
  return nullptr;
}
} // namespace

int main(int argc, char** argv) {
  setvbuf(stderr, nullptr, _IONBF, 0);
  const bool Reregister = argc == 2 && std::strcmp(argv[1], "--reregister") == 0;
  if (argc > 2 || (argc == 2 && !Reregister)) return 64;

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
  std::fprintf(stderr, "DONTUNMAP_INFLIGHT warm H=%p T=%p value=%d reregister=%d\n",
               reinterpret_cast<void*>(SyntheticH), Target, Warm, Reregister ? 1 : 0);
  if (Warm != 111) return 5;

  // Delink the warmed H->T direct branch while preserving this VMA generation.
  if (::mprotect(Target, PageSize, PROT_READ | PROT_WRITE) != 0) return 6;
  EmitReturn(Target, 111);
  if (::mprotect(Target, PageSize, PROT_READ | PROT_EXEC) != 0) return 7;

  if (!PublishTarget(reinterpret_cast<uintptr_t>(Target))) return 8;
  if (!Touch(ArmPath)) return 9;

  pthread_t Worker {};
  if (::pthread_create(&Worker, nullptr, WorkerMain, nullptr) != 0) return 10;
  if (!WaitFor(SelectedPath)) return 11;
  std::fprintf(stderr, "DONTUNMAP_INFLIGHT old-H-redirect-pending H=%p T=%p\n",
               reinterpret_cast<void*>(SyntheticH), Target);

  void* Moved = ::mremap(Target, PageSize, PageSize, MREMAP_MAYMOVE | MREMAP_DONTUNMAP);
  if (Moved == MAP_FAILED) {
    std::fprintf(stderr, "DONTUNMAP_INFLIGHT mremap failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 12;
  }
  if (Moved == Target) return 13;
  const int MovedValue = reinterpret_cast<Fn>(Moved)();
  if (MovedValue != 111) return 14;

  // The source VA survives DONTUNMAP with the same tracked owner. Replace only
  // its contents, without replacing the VMA, to create a same-owner numeric ABA.
  if (::mprotect(Target, PageSize, PROT_READ | PROT_WRITE) != 0) return 15;
  EmitReturn(Target, 222);
  if (::mprotect(Target, PageSize, PROT_READ | PROT_EXEC) != 0) return 16;
  const int OldNow = reinterpret_cast<Fn>(Target)();
  std::fprintf(stderr,
               "DONTUNMAP_INFLIGHT moved old=%p new=%p old-now=%d moved-value=%d reregister=%d\n",
               Target, Moved, OldNow, MovedValue, Reregister ? 1 : 0);
  if (OldNow != 222) return 17;

  if (Reregister) {
    LinkAddressToFunction(SyntheticH, reinterpret_cast<uintptr_t>(Moved));
    std::fprintf(stderr, "DONTUNMAP_INFLIGHT reregistered H=%p T=%p\n",
                 reinterpret_cast<void*>(SyntheticH), Moved);
  }

  if (!Touch(ResumePath)) return 18;
  if (::pthread_join(Worker, nullptr) != 0) return 19;

  const int Result = WorkerValue.load(std::memory_order_acquire);
  std::fprintf(stderr, "DONTUNMAP_INFLIGHT final worker-value=%d reregister=%d\n",
               Result, Reregister ? 1 : 0);
  return Reregister ? (Result == 111 ? 0 : 20) : (Result == 222 ? 0 : 21);
}
