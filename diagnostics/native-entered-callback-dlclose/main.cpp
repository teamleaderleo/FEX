#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <dlfcn.h>
#include <fstream>
#include <string>
#include <thread>
#include <unistd.h>

namespace {
using Callback = int (*)(int, int);

bool AddressMapped(uintptr_t Address) {
  std::ifstream Maps {"/proc/self/maps"};
  std::string Line;
  while (std::getline(Maps, Line)) {
    unsigned long long Begin {};
    unsigned long long End {};
    if (std::sscanf(Line.c_str(), "%llx-%llx", &Begin, &End) == 2 && Address >= Begin && Address < End) {
      return true;
    }
  }
  return false;
}

bool MakePipe(int FDs[2], const char* Name) {
  if (::pipe(FDs) == 0) {
    return true;
  }
  std::fprintf(stderr, "NATIVE_ENTERED pipe %s failed errno=%d (%s)\n", Name, errno, std::strerror(errno));
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

  void* Owner = ::dlopen("./libnative-entered-owner.so", RTLD_NOW | RTLD_LOCAL);
  if (!Owner) {
    std::fprintf(stderr, "NATIVE_ENTERED dlopen failed: %s\n", ::dlerror());
    return 2;
  }

  auto Function = reinterpret_cast<Callback>(::dlsym(Owner, "native_entered_callback"));
  if (!Function) {
    std::fprintf(stderr, "NATIVE_ENTERED dlsym failed: %s\n", ::dlerror());
    return 3;
  }
  const uintptr_t Address = reinterpret_cast<uintptr_t>(Function);

  int Entered[2] {-1, -1};
  int Release[2] {-1, -1};
  if (!MakePipe(Entered, "entered") || !MakePipe(Release, "release")) {
    return 4;
  }

  int Result = -999;
  std::printf("NATIVE_ENTERED target=%p pin=%d mapped=%d\n",
              reinterpret_cast<void*>(Address), Pin ? 1 : 0, AddressMapped(Address) ? 1 : 0);

  std::thread Worker {[&] {
    std::printf("NATIVE_ENTERED worker-call target=%p\n", reinterpret_cast<void*>(Address));
    Result = Function(Entered[1], Release[0]);
    std::printf("NATIVE_ENTERED worker-return value=%d\n", Result);
  }};

  char Marker = 0;
  ssize_t Read;
  do {
    Read = ::read(Entered[0], &Marker, 1);
  } while (Read < 0 && errno == EINTR);
  if (Read != 1 || Marker != 'E') {
    std::fprintf(stderr, "NATIVE_ENTERED failed to observe callback entry read=%zd marker=%d\n", Read, static_cast<int>(Marker));
    return 5;
  }
  std::printf("NATIVE_ENTERED callback-entered target=%p mapped=%d\n",
              reinterpret_cast<void*>(Address), AddressMapped(Address) ? 1 : 0);

  if (Pin) {
    std::printf("NATIVE_ENTERED owner-pinned mapped=%d\n", AddressMapped(Address) ? 1 : 0);
  } else {
    if (::dlclose(Owner) != 0) {
      std::fprintf(stderr, "NATIVE_ENTERED dlclose failed: %s\n", ::dlerror());
      return 6;
    }
    Owner = nullptr;
    const bool Mapped = AddressMapped(Address);
    std::printf("NATIVE_ENTERED owner-closed mapped=%d\n", Mapped ? 1 : 0);
    if (Mapped) {
      return 7;
    }
  }

  const char ReleaseMarker = 'R';
  ssize_t Written;
  do {
    Written = ::write(Release[1], &ReleaseMarker, 1);
  } while (Written < 0 && errno == EINTR);
  if (Written != 1) {
    std::fprintf(stderr, "NATIVE_ENTERED release write failed written=%zd errno=%d (%s)\n",
                 Written, errno, std::strerror(errno));
    return 8;
  }
  std::printf("NATIVE_ENTERED callback-released target=%p\n", reinterpret_cast<void*>(Address));

  Worker.join();
  if (Owner) {
    (void)::dlclose(Owner);
  }
  std::printf("NATIVE_ENTERED final value=%d pin=%d\n", Result, Pin ? 1 : 0);
  return Result == 1023 ? 0 : 9;
}
