#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FIXTURE_ROOT")
    root = Path(sys.argv[1]).resolve() / "fex-full-thunk-pair"
    main_cpp = root / "guest/main.cpp"
    s = main_cpp.read_text()

    s = s.replace(
        'static DSO load_guest(int generation) {\n  DSO d;\n  d.h = dlopen("./guest/liblifetime-guest.so", RTLD_NOW | RTLD_LOCAL);',
        'static DSO load_guest_path(const char* path, int generation) {\n  DSO d;\n  d.h = dlopen(path, RTLD_NOW | RTLD_LOCAL);',
        1,
    )
    anchor = '  d.span = guest_dso_span();\n  return d;\n}\n\nstatic void load_host_thunk_library()'
    repl = '''  d.span = guest_dso_span();
  return d;
}

static DSO load_guest(int generation) {
  return load_guest_path("./guest/liblifetime-guest.so", generation);
}

static void load_host_thunk_library()'''
    if anchor not in s:
        raise SystemExit('load_guest tail anchor missing')
    s = s.replace(anchor, repl, 1)

    s = s.replace(
        '  bool pin = false;\n  int cycles = 1;',
        '  bool pin = false;\n  bool multi_owner = false;\n  int cycles = 1;',
        1,
    )
    s = s.replace(
        '    else if (!std::strcmp(argv[i], "--pin")) pin = true;\n',
        '    else if (!std::strcmp(argv[i], "--pin")) pin = true;\n    else if (!std::strcmp(argv[i], "--multi-owner")) multi_owner = true;\n',
        1,
    )

    anchor = '  if (!host_a || host_a != host_b) return 3;\n\n'
    block = r'''  if (!host_a || host_a != host_b) return 3;

  if (multi_owner) {
    DSO a = load_guest_path("./guest/liblifetime-guest-a.so", 1);
    DSO b = load_guest_path("./guest/liblifetime-guest-b.so", 2001);
    std::printf("multi-owner A invoker           0x%016" PRIxPTR "\n", a.invoker_a);
    std::printf("multi-owner B invoker           0x%016" PRIxPTR "\n", b.invoker_a);
    if (a.invoker_a == b.invoker_a) return 50;

    a.register_a(host_a);
    b.register_a(host_a);
    const int before = reinterpret_cast<LinkedHostFn>(host_a)(5);
    const int want_a = (5 * 3 + 7) + 1 * 1000 + 1;
    std::printf("multi-owner active A            rv=%d want=%d\n", before, want_a);
    if (before != want_a) return 51;

    const uintptr_t old_a = a.invoker_a;
    if (dlclose(a.h) != 0) { std::fprintf(stderr, "multi-owner close A: %s\n", dlerror()); return 52; }
    print_mapping("multi-owner old A after close", old_a);
    print_mapping("multi-owner live B", b.invoker_a);
    if (executable(old_a) || !executable(b.invoker_a)) return 53;

    const int after = reinterpret_cast<LinkedHostFn>(host_a)(9);
    const int want_b = (9 * 3 + 7) + 2001 * 1000 + 1;
    std::printf("multi-owner promoted B          rv=%d want=%d\n", after, want_b);

    if (dlclose(b.h) != 0) return 54;
    return after == want_b ? 0 : 55;
  }

'''
    if anchor not in s:
        raise SystemExit('main multi-owner anchor missing')
    main_cpp.write_text(s.replace(anchor, block, 1))

    makefile = root / "Makefile"
    ms = makefile.read_text()
    old = 'all: guest/liblifetime-guest.so guest/fex_full_lifetime host/lifetime-host.so\n'
    new = 'all: guest/liblifetime-guest.so guest/liblifetime-guest-a.so guest/liblifetime-guest-b.so guest/fex_full_lifetime host/lifetime-host.so\n'
    if old not in ms:
        raise SystemExit('Makefile all anchor missing')
    ms = ms.replace(old, new, 1)
    rule = 'guest/liblifetime-guest.so: guest/guest_dso.cpp\n\t$(GUEST_CXX) $(GUEST_CXXFLAGS) -fPIC -shared -Wl,--build-id=none -o $@ $<\n'
    if rule not in ms:
        raise SystemExit('guest DSO rule missing')
    extra = rule + '\nguest/liblifetime-guest-a.so: guest/guest_dso.cpp\n\t$(GUEST_CXX) $(GUEST_CXXFLAGS) -fPIC -shared -Wl,--build-id=none -o $@ $<\n\nguest/liblifetime-guest-b.so: guest/guest_dso.cpp\n\t$(GUEST_CXX) $(GUEST_CXXFLAGS) -fPIC -shared -Wl,--build-id=none -o $@ $<\n'
    ms = ms.replace(rule, extra, 1)
    ms = ms.replace(
        'rm -f guest/liblifetime-guest.so guest/fex_full_lifetime host/lifetime-host.so',
        'rm -f guest/liblifetime-guest.so guest/liblifetime-guest-a.so guest/liblifetime-guest-b.so guest/fex_full_lifetime host/lifetime-host.so',
        1,
    )
    makefile.write_text(ms)
    print('Patched full thunk fixture with --multi-owner')


if __name__ == '__main__':
    main()
