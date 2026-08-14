#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wmissing-field-initializers"
#include "common/Guest.h"
#pragma GCC diagnostic pop

#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <sys/ipc.h>
#include <sys/mman.h>
#include <sys/shm.h>
#include <unistd.h>

namespace {
constexpr uintptr_t H = 0x0000700000060000ULL;
using Fn = int (*)();

void Emit(void* p, uint32_t value) {
  auto* c = static_cast<unsigned char*>(p);
  c[0] = 0xB8;
  std::memcpy(&c[1], &value, sizeof(value));
  c[5] = 0xC3;
  __builtin___clear_cache(reinterpret_cast<char*>(p), reinterpret_cast<char*>(p) + 6);
}

int Run(bool Reregister, bool InspectOnly) {
  const long ps = ::sysconf(_SC_PAGESIZE);
  if (ps <= 0) return 2;
  const size_t page = static_cast<size_t>(ps);

  const int shmid = ::shmget(IPC_PRIVATE, page, IPC_CREAT | 0700);
  if (shmid < 0) {
    std::fprintf(stderr, "SHMDT_VIEW shmget failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 3;
  }

  void* rw = ::shmat(shmid, nullptr, 0);
  void* exec_old = ::shmat(shmid, nullptr, SHM_EXEC);
  void* exec_new = ::shmat(shmid, nullptr, SHM_EXEC);
  if (rw == reinterpret_cast<void*>(-1) || exec_old == reinterpret_cast<void*>(-1) || exec_new == reinterpret_cast<void*>(-1)) {
    std::fprintf(stderr, "SHMDT_VIEW shmat failed rw=%p old=%p new=%p errno=%d (%s)\n",
                 rw, exec_old, exec_new, errno, std::strerror(errno));
    if (rw != reinterpret_cast<void*>(-1)) (void)::shmdt(rw);
    if (exec_old != reinterpret_cast<void*>(-1)) (void)::shmdt(exec_old);
    if (exec_new != reinterpret_cast<void*>(-1)) (void)::shmdt(exec_new);
    (void)::shmctl(shmid, IPC_RMID, nullptr);
    return 4;
  }

  if (::shmctl(shmid, IPC_RMID, nullptr) != 0) {
    std::fprintf(stderr, "SHMDT_VIEW IPC_RMID failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 5;
  }

  Emit(rw, 111);
  auto old_direct = reinterpret_cast<Fn>(exec_old);
  auto new_direct = reinterpret_cast<Fn>(exec_new);
  const int old_before = old_direct();
  const int new_before = new_direct();
  std::fprintf(stderr,
               "SHMDT_VIEW attached shmid=%d rw=%p old=%p new=%p direct-old=%d direct-new=%d reregister=%d inspect=%d\n",
               shmid, rw, exec_old, exec_new, old_before, new_before,
               Reregister ? 1 : 0, InspectOnly ? 1 : 0);
  if (old_before != 111 || new_before != 111) return 6;

  LinkAddressToFunction(H, reinterpret_cast<uintptr_t>(exec_old));
  auto linked = reinterpret_cast<Fn>(H);
  const int warm = linked();
  std::fprintf(stderr, "SHMDT_VIEW warm H=%p T-old=%p value=%d\n", reinterpret_cast<void*>(H), exec_old, warm);
  if (warm != 111) return 7;

  if (::shmdt(exec_old) != 0) {
    std::fprintf(stderr, "SHMDT_VIEW detach-old failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 8;
  }
  std::fprintf(stderr, "SHMDT_VIEW detached-old old=%p surviving-rw=%p surviving-exec=%p\n",
               exec_old, rw, exec_new);

  const int surviving = new_direct();
  if (surviving != 111) {
    std::fprintf(stderr, "SHMDT_VIEW surviving exec value=%d\n", surviving);
    return 9;
  }

  void* reused = ::mmap(exec_old, page, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
  if (reused == MAP_FAILED || reused != exec_old) {
    std::fprintf(stderr, "SHMDT_VIEW reuse failed result=%p errno=%d (%s)\n",
                 reused, errno, std::strerror(errno));
    return 10;
  }
  Emit(reused, 333);
  if (::mprotect(reused, page, PROT_READ | PROT_EXEC) != 0) {
    std::fprintf(stderr, "SHMDT_VIEW reuse mprotect failed errno=%d (%s)\n", errno, std::strerror(errno));
    return 11;
  }
  auto reused_direct = reinterpret_cast<Fn>(reused);
  const int reused_value = reused_direct();
  std::fprintf(stderr, "SHMDT_VIEW reused-old old=%p direct=%d surviving-exec-value=%d\n",
               reused, reused_value, surviving);
  if (reused_value != 333) return 12;

  if (InspectOnly) {
    std::fprintf(stderr, "SHMDT_VIEW inspect detached=1 reused=1 surviving=111 replacement=333\n");
    return 0;
  }

  if (Reregister) {
    LinkAddressToFunction(H, reinterpret_cast<uintptr_t>(exec_new));
    std::fprintf(stderr, "SHMDT_VIEW reregister H=%p T-new=%p\n", reinterpret_cast<void*>(H), exec_new);
  }

  const int value = linked();
  std::fprintf(stderr, "SHMDT_VIEW final H-value=%d reregister=%d repaired-expected=%d\n",
               value, Reregister ? 1 : 0, Reregister ? 111 : -1);
  return Reregister ? (value == 111 ? 0 : 13) : (value == 333 ? 0 : 14);
}
} // namespace

int main(int argc, char** argv) {
  setvbuf(stderr, nullptr, _IONBF, 0);
  if (argc == 1) return Run(false, false);
  if (argc == 2 && std::strcmp(argv[1], "--reregister") == 0) return Run(true, false);
  if (argc == 2 && std::strcmp(argv[1], "--inspect-only") == 0) return Run(false, true);
  return 64;
}
