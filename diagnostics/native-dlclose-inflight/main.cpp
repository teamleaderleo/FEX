#include <atomic>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <fstream>
#include <string>
#include <thread>
#include <unistd.h>

namespace {
using Fn = int (*)();
std::atomic<bool> Selected {false};
std::atomic<bool> Resume {false};
std::atomic<int> Result {-1};

bool AddressMapped(uintptr_t Address) {
  std::ifstream Maps {"/proc/self/maps"};
  std::string Line;
  while (std::getline(Maps, Line)) {
    unsigned long long Begin {};
    unsigned long long End {};
    if (std::sscanf(Line.c_str(), "%llx-%llx", &Begin, &End) == 2 &&
        Address >= Begin && Address < End) {
      return true;
    }
  }
  return false;
}
} // namespace

int main(int argc, char** argv) {
  setvbuf(stdout, nullptr, _IONBF, 0);
  setvbuf(stderr, nullptr, _IONBF, 0);
  const bool Pin = argc == 2 && std::strcmp(argv[1], "--pin") == 0;
  if (argc > 2 || (argc == 2 && !Pin)) {
    std::fprintf(stderr, "usage: %s [--pin]\n", argv[0]);
    return 64;
  }

  void* Owner = ::dlopen("./libnative-inflight-owner.so", RTLD_NOW | RTLD_LOCAL);
  if (!Owner) {
    std::fprintf(stderr, "NATIVE_INFLIGHT dlopen failed: %s\n", ::dlerror());
    return 2;
  }

  auto Function = reinterpret_cast<Fn>(::dlsym(Owner, "native_owner_fn"));
  if (!Function) {
    std::fprintf(stderr, "NATIVE_INFLIGHT dlsym failed: %s\n", ::dlerror());
    return 3;
  }

  const uintptr_t Address = reinterpret_cast<uintptr_t>(Function);
  std::printf("NATIVE_INFLIGHT target=%p pin=%d mapped=%d\n",
              reinterpret_cast<void*>(Address), Pin ? 1 : 0, AddressMapped(Address) ? 1 : 0);

  std::thread Worker {[Function] {
    // Function is now captured in this thread's local callable state. The
    // barrier deliberately sits after selection and immediately before call.
    Selected.store(true, std::memory_order_release);
    std::printf("NATIVE_INFLIGHT selected target=%p\n", reinterpret_cast<void*>(Function));
    while (!Resume.load(std::memory_order_acquire)) {
      ::usleep(1000);
    }
    std::printf("NATIVE_INFLIGHT resume target=%p\n", reinterpret_cast<void*>(Function));
    const int Value = Function();
    Result.store(Value, std::memory_order_release);
    std::printf("NATIVE_INFLIGHT worker-return value=%d\n", Value);
  }};

  while (!Selected.load(std::memory_order_acquire)) {
    ::usleep(1000);
  }

  if (Pin) {
    std::printf("NATIVE_INFLIGHT owner-pinned mapped=%d\n", AddressMapped(Address) ? 1 : 0);
  } else {
    if (::dlclose(Owner) != 0) {
      std::fprintf(stderr, "NATIVE_INFLIGHT dlclose failed: %s\n", ::dlerror());
      return 4;
    }
    Owner = nullptr;
    const bool Mapped = AddressMapped(Address);
    std::printf("NATIVE_INFLIGHT owner-closed mapped=%d\n", Mapped ? 1 : 0);
    if (Mapped) {
      std::fprintf(stderr, "NATIVE_INFLIGHT expected owner mapping to disappear\n");
      return 5;
    }
  }

  Resume.store(true, std::memory_order_release);
  Worker.join();

  const int Value = Result.load(std::memory_order_acquire);
  if (Owner) {
    (void)::dlclose(Owner);
  }
  std::printf("NATIVE_INFLIGHT final value=%d pin=%d\n", Value, Pin ? 1 : 0);
  return Value == 1023 ? 0 : 6;
}
