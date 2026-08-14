#include <wayland-client.h>
#include <wayland-util.h>
#include "common/Guest.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

template<char> struct ArgType;
template<> struct ArgType<'s'> { using type = const char*; };
template<> struct ArgType<'u'> { using type = uint32_t; };
template<> struct ArgType<'i'> { using type = int32_t; };
template<> struct ArgType<'o'> { using type = wl_proxy*; };
template<> struct ArgType<'n'> { using type = wl_proxy*; };
template<> struct ArgType<'a'> { using type = wl_array*; };
template<> struct ArgType<'f'> { using type = wl_fixed_t; };
template<> struct ArgType<'h'> { using type = int32_t; };
template<char... S> static uint64_t Allocate(void (*callback)()) {
  using cb = void(void*, wl_proxy*, typename ArgType<S>::type...);
  return static_cast<uint64_t>(reinterpret_cast<uintptr_t>(reinterpret_cast<void*>(AllocateHostTrampolineForGuestFunction((cb*)callback))));
}
extern "C" uint64_t FEXWaylandAllocateResidentListener(void (*callback)(), const char* s) {
#define W(sig, ...) if (!std::strcmp(s, sig)) return Allocate<__VA_ARGS__>(callback)
  W(""); W("a",'a'); W("f",'f'); W("hu",'h','u'); W("i",'i'); W("if",'i','f'); W("iff",'i','f','f'); W("ii",'i','i'); W("iu",'i','u');
  W("iia",'i','i','a'); W("iiii",'i','i','i','i'); W("iiiiissi",'i','i','i','i','i','s','s','i'); W("n",'n'); W("o",'o'); W("u",'u');
  W("uff",'u','f','f'); W("uffff",'u','f','f','f','f'); W("uhu",'u','h','u'); W("ui",'u','i'); W("uiff",'u','i','f','f');
  W("uiii",'u','i','i','i'); W("uiiii",'u','i','i','i','i'); W("uo",'u','o'); W("uoa",'u','o','a'); W("uoff",'u','o','f','f');
  W("uoffo",'u','o','f','f','o'); W("uoo",'u','o','o'); W("us",'u','s'); W("uss",'u','s','s'); W("usu",'u','s','u'); W("uu",'u','u');
  W("uuf",'u','u','f'); W("uui",'u','u','i'); W("uuoiff",'u','u','o','i','f','f'); W("uuou",'u','u','o','u'); W("uuu",'u','u','u');
  W("uuuu",'u','u','u','u'); W("uuuuu",'u','u','u','u','u'); W("s",'s'); W("ss",'s','s'); W("sii",'s','i','i');
#undef W
  std::fprintf(stderr, "Unknown resident Wayland listener signature: %s\n", s); std::abort();
}
