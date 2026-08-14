#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FIXTURE_ROOT")
    root = Path(sys.argv[1]).resolve()
    p = root / "fex-full-thunk-pair/guest/main.cpp"
    s = p.read_text()
    s = s.replace("#include <algorithm>\n", "#include <algorithm>\n#include <atomic>\n#include <thread>\n", 1)
    s = s.replace("  bool pin = false;\n  int cycles = 1;", "  bool pin = false;\n  bool thread_cache = false;\n  int cycles = 1;", 1)
    s = s.replace(
        '    else if (!std::strcmp(argv[i], "--pin")) pin = true;\n',
        '    else if (!std::strcmp(argv[i], "--pin")) pin = true;\n    else if (!std::strcmp(argv[i], "--thread-cache")) thread_cache = true;\n',
        1,
    )
    anchor = "  if (!host_a || host_a != host_b) return 3;\n\n"
    block = r'''  if (!host_a || host_a != host_b) return 3;

  if (thread_cache) {
    DSO d = load_guest(1);
    d.register_a(host_a);
    const int want_before = (5 * 3 + 7) + 1000 + 1;
    const int want_after = (9 * 3 + 7) + 1001 * 1000 + 1;
    std::atomic<int> phase {0};
    std::atomic<int> before {0};
    std::atomic<int> after {0};

    std::thread worker([&] {
      before.store(reinterpret_cast<LinkedHostFn>(host_a)(5), std::memory_order_release);
      phase.store(1, std::memory_order_release);
      while (phase.load(std::memory_order_acquire) == 1) std::this_thread::yield();
      after.store(reinterpret_cast<LinkedHostFn>(host_a)(9), std::memory_order_release);
      phase.store(3, std::memory_order_release);
    });

    while (phase.load(std::memory_order_acquire) != 1) std::this_thread::yield();
    std::printf("thread-cache preheat             rv=%d want=%d\n", before.load(), want_before);
    if (before.load() != want_before) return 40;

    const uintptr_t old_invoker = d.invoker_a;
    const Span old_span = d.span;
    if (dlclose(d.h) != 0) { std::fprintf(stderr, "thread-cache dlclose: %s\n", dlerror()); return 41; }
    print_mapping("thread-cache old invoker", old_invoker);
    if (executable(old_invoker)) return 42;

    const size_t reservation_len = old_span.hi - old_span.lo;
    void* reservation = reserve_span(old_span);
    if (reservation == MAP_FAILED) {
      std::fprintf(stderr, "thread-cache reserve: %s\n", std::strerror(errno));
      return 43;
    }
    std::printf("thread-cache reserved old span   %p len=0x%zx\n", reservation, reservation_len);

    DSO newer = load_guest(1001);
    std::printf("thread-cache reload invoker      old=0x%016" PRIxPTR " new=0x%016" PRIxPTR " %s\n",
                old_invoker, newer.invoker_a, old_invoker == newer.invoker_a ? "SAME" : "DIFFERENT");
    newer.register_a(host_a);
    phase.store(2, std::memory_order_release);
    while (phase.load(std::memory_order_acquire) != 3) std::this_thread::yield();
    worker.join();

    std::printf("thread-cache post-reload         rv=%d want=%d\n", after.load(), want_after);
    if (dlclose(newer.h) != 0) return 44;
    munmap(reservation, reservation_len);
    return after.load() == want_after ? 0 : 45;
  }

'''
    if anchor not in s:
        raise SystemExit("main insertion anchor missing")
    p.write_text(s.replace(anchor, block, 1))

    m = root / "fex-full-thunk-pair/Makefile"
    ms = m.read_text()
    old = "$(GUEST_CXX) $(GUEST_CXXFLAGS) -o $@ $< -ldl\n"
    if old not in ms:
        raise SystemExit("Makefile guest executable anchor missing")
    m.write_text(ms.replace(old, "$(GUEST_CXX) $(GUEST_CXXFLAGS) -o $@ $< -ldl -pthread\n", 1))
    print("Patched full-thunk fixture with --thread-cache mode")


if __name__ == "__main__":
    main()
