#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FIXTURE_ROOT")
    root = Path(sys.argv[1]).resolve() / "fex-full-thunk-pair"
    guest = root / "guest"

    makefile = r'''GUEST_CXX ?= g++
HOST_CXX ?= g++
GUEST_CXXFLAGS ?= -O2 -g -std=c++20 -Wall -Wextra -Werror
HOST_CXXFLAGS ?= -O2 -g -std=c++20 -Wall -Wextra -Werror

all: guest/liblifetime-bridge.so guest/liblifetime-guest.so guest/fex_full_lifetime host/lifetime-host.so

guest/liblifetime-bridge.so: guest/bridge_dso.cpp
	$(GUEST_CXX) $(GUEST_CXXFLAGS) -fPIC -shared -Wl,--build-id=none -Wl,-z,nodelete -Wl,-soname,liblifetime-bridge.so -o $@ $<

guest/liblifetime-guest.so: guest/guest_dso.cpp guest/liblifetime-bridge.so
	$(GUEST_CXX) $(GUEST_CXXFLAGS) -fPIC -shared -Wl,--build-id=none -Wl,-rpath,'$$ORIGIN' -o $@ $< guest/liblifetime-bridge.so

guest/fex_full_lifetime: guest/main.cpp
	$(GUEST_CXX) $(GUEST_CXXFLAGS) -rdynamic -o $@ $< -ldl

host/lifetime-host.so: host/host_dso.cpp
	$(HOST_CXX) $(HOST_CXXFLAGS) -fPIC -shared -Wl,--build-id=none -o $@ $<

clean:
	rm -f guest/liblifetime-bridge.so guest/liblifetime-guest.so guest/fex_full_lifetime host/lifetime-host.so
'''
    (root / "Makefile").write_text(makefile)

    bridge = r'''#include <cstdint>

#define DEFINE_MAGIC_THUNK(symbol, bytes) \
  extern "C" __attribute__((visibility("hidden"))) int symbol(void*); \
  asm(".text\n" #symbol ":\n.byte 0x0f, 0x3f\n.byte " bytes "\n")

// sha256("lifetime:invoke_host_int")
DEFINE_MAGIC_THUNK(
  lifetime_invoke_host_int_thunk,
  "0x99,0x4e,0x95,0x3f,0x95,0xbc,0x4c,0x4f,0x7b,0x17,0x09,0xee,0x98,0x04,0x8a,0xc9,"
  "0x6c,0x55,0x4e,0xc1,0x87,0x07,0x05,0xc9,0xe4,0x4e,0xb1,0x31,0xe3,0x37,0xc5,0x93");

static inline uintptr_t native_address_from_r11() {
#if defined(__x86_64__)
  register uintptr_t host_address asm("r11");
  asm volatile("" : "=r"(host_address));
  return host_address;
#else
#error This bridge DSO must be built for x86-64.
#endif
}

static inline int call_native_explicit(uintptr_t host_address, int x) {
  struct __attribute__((packed)) Args {
    int a0;
    uint64_t host_address;
    int rv;
  } args {x, host_address, 0};
  lifetime_invoke_host_int_thunk(&args);
  return args.rv;
}

// These are process-resident, generation-neutral CallHostFunction surrogates.
extern "C" __attribute__((visibility("default"), noinline))
int lifetime_bridge_callhost_a(int x) {
  return call_native_explicit(native_address_from_r11(), x) + 1;
}

extern "C" __attribute__((visibility("default"), noinline))
int lifetime_bridge_callhost_b(int x) {
  return call_native_explicit(native_address_from_r11(), x) + 2;
}

struct __attribute__((packed)) CallbackArgs {
  int a0;
  int rv;
};

// Fixed callback unpacker is resident. GuestTarget can belong to some other
// guest owner (the main executable in this fixture, analogous to Vulkan's X11
// callback target living outside libvulkan-guest.so).
extern "C" __attribute__((visibility("default"), noinline))
void lifetime_bridge_guest_unpacker(uintptr_t target, void* argsv) {
  auto* args = reinterpret_cast<CallbackArgs*>(argsv);
  auto fn = reinterpret_cast<int (*)(int)>(target);
  args->rv = fn(args->a0);
}
'''
    (guest / "bridge_dso.cpp").write_text(bridge)

    wrapper = r'''#include <cstdint>

#define DEFINE_MAGIC_THUNK(symbol, bytes) \
  extern "C" __attribute__((visibility("hidden"))) int symbol(void*); \
  asm(".text\n" #symbol ":\n.byte 0x0f, 0x3f\n.byte " bytes "\n")

DEFINE_MAGIC_THUNK(
  fex_builtin_link_address,
  "0xe6,0xa8,0xec,0x1c,0x7b,0x74,0x35,0x27,0xe9,0x4f,0x5b,0x6e,0x2d,0xc9,0xa0,0x27,"
  "0xd6,0x1f,0x2b,0x87,0x8f,0x2d,0x35,0x50,0xea,0x16,0xb8,0xc4,0x5e,0x42,0xfd,0x77");

extern "C" int lifetime_bridge_callhost_a(int);
extern "C" int lifetime_bridge_callhost_b(int);
extern "C" void lifetime_bridge_guest_unpacker(uintptr_t, void*);

static int generation;

static inline void link_address(uintptr_t native_address, uintptr_t guest_target) {
  struct Args { uint64_t original_callee; uint64_t target_addr; } args {native_address, guest_target};
  fex_builtin_link_address(&args);
}

extern "C" __attribute__((visibility("default"), noinline))
void lifetime_set_generation(int g) { generation = g; }

extern "C" __attribute__((visibility("default"), noinline))
void lifetime_register_a(uintptr_t host_address) {
  link_address(host_address, reinterpret_cast<uintptr_t>(&lifetime_bridge_callhost_a));
}

extern "C" __attribute__((visibility("default"), noinline))
void lifetime_register_b(uintptr_t host_address) {
  link_address(host_address, reinterpret_cast<uintptr_t>(&lifetime_bridge_callhost_b));
}

extern "C" __attribute__((visibility("default"), noinline))
uintptr_t lifetime_callhost_a_address() { return reinterpret_cast<uintptr_t>(&lifetime_bridge_callhost_a); }

extern "C" __attribute__((visibility("default"), noinline))
uintptr_t lifetime_callhost_b_address() { return reinterpret_cast<uintptr_t>(&lifetime_bridge_callhost_b); }

// Wrapper-specific function proves physical unload/reload resets wrapper state.
extern "C" __attribute__((visibility("default"), noinline))
int lifetime_wrapper_generation() { return generation; }

extern "C" __attribute__((visibility("default"), noinline))
uintptr_t lifetime_bridge_unpacker_address() { return reinterpret_cast<uintptr_t>(&lifetime_bridge_guest_unpacker); }
'''
    (guest / "guest_dso.cpp").write_text(wrapper)

    main_path = guest / "main.cpp"
    text = main_path.read_text()

    text = text.replace('using DirectHostCall = int (*)(uintptr_t, int);\n', 'using WrapperGeneration = int (*)();\n')
    text = text.replace('  DirectHostCall direct_host_call{};\n  GetAddress target_address{};\n  GetAddress unpacker_address{};\n',
                        '  WrapperGeneration wrapper_generation{};\n  GetAddress unpacker_address{};\n')
    text = text.replace('  uintptr_t invoker_a{}, invoker_b{}, target{}, unpacker{};\n',
                        '  uintptr_t invoker_a{}, invoker_b{}, unpacker{};\n')
    text = text.replace('  d.direct_host_call = sym<DirectHostCall>(d.h, "lifetime_direct_host_call");\n  d.target_address = sym<GetAddress>(d.h, "lifetime_guest_target_address");\n  d.unpacker_address = sym<GetAddress>(d.h, "lifetime_guest_unpacker_address");\n',
                        '  d.wrapper_generation = sym<WrapperGeneration>(d.h, "lifetime_wrapper_generation");\n  d.unpacker_address = sym<GetAddress>(d.h, "lifetime_bridge_unpacker_address");\n')
    text = text.replace('  d.target = d.target_address();\n', '')

    anchor = 'static void load_host_thunk_library() {'
    stable_target = r'''extern "C" __attribute__((visibility("default"), noinline))
int lifetime_process_guest_target(int x) {
  return 70000 + x * 10 + 3;
}

static uintptr_t process_guest_target_address() {
  return reinterpret_cast<uintptr_t>(&lifetime_process_guest_target);
}

static int bridge_map_count() {
  int count = 0;
  for (const auto& m : maps()) {
    if (m.path.find("liblifetime-bridge.so") != std::string::npos) ++count;
  }
  return count;
}

'''
    if anchor not in text:
        raise SystemExit("main anchor missing")
    text = text.replace(anchor, stable_target + anchor, 1)

    text = text.replace('    print_mapping("GuestTarget", d.target);\n',
                        '    const uintptr_t callback_target = process_guest_target_address();\n    print_mapping("process GuestTarget", callback_target);\n')
    text = text.replace('    std::printf("guest DSO span                   %016" PRIxPTR "-%016" PRIxPTR "\\n", d.span.lo, d.span.hi);\n',
                        '    std::printf("guest DSO span                   %016" PRIxPTR "-%016" PRIxPTR "\\n", d.span.lo, d.span.hi);\n    std::printf("resident bridge maps            %d\\n", bridge_map_count());\n    std::printf("wrapper generation              %d\\n", d.wrapper_generation());\n')
    text = text.replace('    const int want_callhost = (5 * 3 + 7) + gen * 1000 + 1;\n',
                        '    const int want_callhost = (5 * 3 + 7) + 1;\n')
    text = text.replace('    const int cb_before = register_and_call_guest_callback(d.target, d.unpacker, 5);\n    const int want_cb = gen * 10000 + 53;\n',
                        '    const int cb_before = register_and_call_guest_callback(callback_target, d.unpacker, 5);\n    const int want_cb = 70000 + 53;\n')
    text = text.replace('    const uintptr_t old_target = d.target;\n', '    const uintptr_t old_target = callback_target;\n')
    text = text.replace('    print_mapping("old target after dlclose", old_target);\n',
                        '    print_mapping("stable target after dlclose", old_target);\n')
    text = text.replace('    if (executable(old_invoker) || executable(old_target) || executable(old_unpacker)) {\n      std::fprintf(stderr, "FAIL: old guest executable mapping survived dlclose\\n");\n      return 14;\n    }\n    std::printf("proof: all embedded guest executable addresses lost mappings\\n");\n',
                        '    if (!executable(old_invoker) || !executable(old_target) || !executable(old_unpacker)) {\n      std::fprintf(stderr, "FAIL: resident bridge/process callback dependency disappeared\\n");\n      return 14;\n    }\n    if (guest_dso_span().lo != 0) {\n      std::fprintf(stderr, "FAIL: unloadable wrapper still mapped after dlclose\\n");\n      return 16;\n    }\n    std::printf("proof: wrapper unmapped while bridge dependencies remain executable\\n");\n    const int split_h_after = reinterpret_cast<LinkedHostFn>(host_a)(7);\n    const int split_cb_after = call_first_callback(7);\n    std::printf("split retained Link after close   rv=%d want=%d\\n", split_h_after, (7 * 3 + 7) + 1);\n    std::printf("split retained callback after close rv=%d want=%d\\n", split_cb_after, 70000 + 73);\n    if (split_h_after != (7 * 3 + 7) + 1 || split_cb_after != 70000 + 73) return 17;\n')
    text = text.replace('    const int direct_fresh = newer.direct_host_call(host_a, 5);\n    std::printf("fresh guest direct host call     rv=%d want=%d\\n", direct_fresh, (5 * 3 + 7) + (gen + 1000) * 1000 + 9);\n\n    const int current_fresh = register_and_call_guest_callback(newer.target, newer.unpacker, 5);\n    std::printf("fresh/current callback            rv=%d want=%d\\n", current_fresh, (gen + 1000) * 10000 + 53);\n',
                        '    std::printf("reloaded wrapper generation       %d want=%d\\n", newer.wrapper_generation(), gen + 1000);\n    if (newer.wrapper_generation() != gen + 1000) return 18;\n\n    const int current_fresh = register_and_call_guest_callback(callback_target, newer.unpacker, 5);\n    std::printf("fresh/current callback            rv=%d want=%d\\n", current_fresh, 70000 + 53);\n')

    # The old adapter and unpacker live outside the wrapper span; reserving the
    # wrapper span should still force the wrapper itself to move.
    main_path.write_text(text)
    print("Patched fixture for process-resident split bridge")


if __name__ == "__main__":
    main()
