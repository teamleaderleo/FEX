#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FIXTURE_ROOT")
    root = Path(sys.argv[1]).resolve()
    p = root / "fex-full-thunk-pair/guest/main.cpp"
    s = p.read_text()

    old = "  bool pin = false;\n  bool thread_cache = false;\n  int cycles = 1;"
    new = "  bool pin = false;\n  bool thread_cache = false;\n  bool inflight_selection = false;\n  int cycles = 1;"
    if old not in s:
        raise SystemExit("mode declaration anchor missing")
    s = s.replace(old, new, 1)

    old = '    else if (!std::strcmp(argv[i], "--thread-cache")) thread_cache = true;\n'
    new = old + '    else if (!std::strcmp(argv[i], "--inflight-selection")) inflight_selection = true;\n'
    if old not in s:
        raise SystemExit("mode parser anchor missing")
    s = s.replace(old, new, 1)

    anchor = "  if (thread_cache) {\n"
    block = r'''  if (inflight_selection) {
    DSO d = load_guest(1);
    d.register_a(host_a);
    const uintptr_t old_invoker = d.invoker_a;
    const Span old_span = d.span;
    const int want_old = (5 * 3 + 7) + 1000 + 1;
    const int want_new = (9 * 3 + 7) + 1001 * 1000 + 1;

    const char* barrier_dir = "/tmp/fex-fieldwork";
    (void)barrier_dir;
    unlink("/tmp/fex-fieldwork/target");
    unlink("/tmp/fex-fieldwork/selected");
    unlink("/tmp/fex-fieldwork/resume");
    FILE* target = std::fopen("/tmp/fex-fieldwork/target", "w");
    if (!target) { std::perror("inflight target"); return 60; }
    std::fprintf(target, "%" PRIxPTR "\n", old_invoker);
    std::fclose(target);
    std::printf("inflight target                  T1=0x%016" PRIxPTR " H=0x%016" PRIxPTR " pin=%d\n",
                old_invoker, host_a, pin ? 1 : 0);
    std::fflush(stdout);

    std::atomic<int> phase {0};
    std::atomic<int> worker_result {0};
    std::thread worker([&] {
      phase.store(1, std::memory_order_release);
      worker_result.store(reinterpret_cast<LinkedHostFn>(host_a)(5), std::memory_order_release);
      phase.store(2, std::memory_order_release);
    });

    while (phase.load(std::memory_order_acquire) == 0) std::this_thread::yield();
    while (access("/tmp/fex-fieldwork/selected", F_OK) != 0) std::this_thread::yield();
    std::printf("inflight selected                T1=0x%016" PRIxPTR "\n", old_invoker);
    std::fflush(stdout);

    if (!pin) {
      if (dlclose(d.h) != 0) { std::fprintf(stderr, "inflight dlclose: %s\n", dlerror()); return 61; }
      print_mapping("inflight old invoker after dlclose", old_invoker);
      if (executable(old_invoker)) return 62;
      std::printf("inflight owner unmapped before resume\n");
      std::fflush(stdout);
    } else {
      std::printf("inflight pin keeps owner mapped before resume\n");
      std::fflush(stdout);
    }

    FILE* resume = std::fopen("/tmp/fex-fieldwork/resume", "w");
    if (!resume) { std::perror("inflight resume"); return 63; }
    std::fputs("resume\n", resume);
    std::fclose(resume);

    worker.join();
    const int observed_old = worker_result.load(std::memory_order_acquire);
    std::printf("inflight worker returned         rv=%d want-old=%d owner-was-%s\n",
                observed_old, want_old, pin ? "mapped" : "unmapped");
    std::fflush(stdout);

    if (pin) {
      if (dlclose(d.h) != 0) return 64;
      return observed_old == want_old ? 0 : 65;
    }

    const size_t reservation_len = old_span.hi - old_span.lo;
    void* reservation = reserve_span(old_span);
    if (reservation == MAP_FAILED) {
      std::fprintf(stderr, "inflight reserve: %s\n", std::strerror(errno));
      return 66;
    }
    DSO newer = load_guest(1001);
    std::printf("inflight reload invoker          old=0x%016" PRIxPTR " new=0x%016" PRIxPTR " %s\n",
                old_invoker, newer.invoker_a, old_invoker == newer.invoker_a ? "SAME" : "DIFFERENT");
    newer.register_a(host_a);
    const int fresh = reinterpret_cast<LinkedHostFn>(host_a)(9);
    std::printf("inflight fresh generation        rv=%d want-new=%d\n", fresh, want_new);
    std::fflush(stdout);
    if (dlclose(newer.h) != 0) return 67;
    munmap(reservation, reservation_len);

    if (fresh != want_new) return 68;
    if (observed_old == want_old) {
      std::printf("INFLIGHT_RESULT stale-selected-generation-executed-after-owner-unmap\n");
      return 50;
    }
    std::printf("INFLIGHT_RESULT selected-generation-did-not-return-old-value rv=%d\n", observed_old);
    return 51;
  }

'''
    if anchor not in s:
        raise SystemExit("thread-cache block anchor missing")
    s = s.replace(anchor, block + anchor, 1)
    p.write_text(s)
    print("Patched full-thunk fixture with --inflight-selection mode")


if __name__ == "__main__":
    main()
