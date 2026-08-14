#!/usr/bin/env python3
from pathlib import Path
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FIXTURE_ROOT")
    p = Path(sys.argv[1]).resolve() / "fex-full-thunk-pair/guest/main.cpp"
    s = p.read_text()

    s = replace_once(
        s,
        '#include <algorithm>\n',
        '#include <algorithm>\n#include <atomic>\n',
        'atomic include')
    s = replace_once(
        s,
        '#include <string>\n#include <vector>\n',
        '#include <string>\n#include <thread>\n#include <vector>\n',
        'thread include')

    s = replace_once(
        s,
        '  bool pin = false;\n  int cycles = 1;\n',
        '  bool pin = false;\n  bool split_inflight = false;\n  int cycles = 1;\n',
        'mode declaration')
    s = replace_once(
        s,
        '    else if (!std::strcmp(argv[i], "--pin")) pin = true;\n',
        '    else if (!std::strcmp(argv[i], "--pin")) pin = true;\n'
        '    else if (!std::strcmp(argv[i], "--split-inflight")) split_inflight = true;\n',
        'mode parser')

    anchor = '  for (int gen = 1; gen <= cycles; ++gen) {\n'
    block = r'''  if (split_inflight) {
    DSO d = load_guest(1);
    const uintptr_t wrapper_probe = reinterpret_cast<uintptr_t>(d.wrapper_generation);
    const Span old_span = d.span;
    d.register_a(host_a);
    const uintptr_t bridge_invoker = d.invoker_a;
    const int want_selected = (5 * 3 + 7) + 1;
    const int want_fresh = (9 * 3 + 7) + 1;

    unlink("/tmp/fex-fieldwork/target");
    unlink("/tmp/fex-fieldwork/selected");
    unlink("/tmp/fex-fieldwork/resume");
    FILE* target = std::fopen("/tmp/fex-fieldwork/target", "w");
    if (!target) { std::perror("split inflight target"); return 70; }
    std::fprintf(target, "%" PRIxPTR "\n", bridge_invoker);
    std::fclose(target);

    std::printf("split inflight target            bridge=0x%016" PRIxPTR " wrapper=0x%016" PRIxPTR " H=0x%016" PRIxPTR "\n",
                bridge_invoker, wrapper_probe, host_a);
    print_mapping("split bridge before select", bridge_invoker);
    print_mapping("split wrapper before select", wrapper_probe);
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
    std::printf("split inflight selected          bridge=0x%016" PRIxPTR "\n", bridge_invoker);
    std::fflush(stdout);

    if (dlclose(d.h) != 0) { std::fprintf(stderr, "split inflight dlclose: %s\n", dlerror()); return 71; }
    print_mapping("split wrapper after dlclose", wrapper_probe);
    print_mapping("split bridge after dlclose", bridge_invoker);
    if (executable(wrapper_probe)) {
      std::fprintf(stderr, "FAIL: split wrapper still executable after dlclose\n");
      return 72;
    }
    if (!executable(bridge_invoker)) {
      std::fprintf(stderr, "FAIL: resident bridge disappeared after wrapper dlclose\n");
      return 73;
    }
    std::printf("split inflight wrapper unmapped before resume; bridge resident\n");
    std::fflush(stdout);

    FILE* resume = std::fopen("/tmp/fex-fieldwork/resume", "w");
    if (!resume) { std::perror("split inflight resume"); return 74; }
    std::fputs("resume\n", resume);
    std::fclose(resume);

    worker.join();
    const int observed = worker_result.load(std::memory_order_acquire);
    std::printf("split inflight worker returned   rv=%d want=%d\n", observed, want_selected);
    std::fflush(stdout);
    if (observed != want_selected) return 75;

    const size_t reservation_len = old_span.hi - old_span.lo;
    void* reservation = reserve_span(old_span);
    if (reservation == MAP_FAILED) {
      std::fprintf(stderr, "split inflight reserve: %s\n", std::strerror(errno));
      return 76;
    }
    DSO newer = load_guest(1001);
    std::printf("split inflight reload wrapper    old=0x%016" PRIxPTR " new=0x%016" PRIxPTR " %s\n",
                wrapper_probe, reinterpret_cast<uintptr_t>(newer.wrapper_generation),
                wrapper_probe == reinterpret_cast<uintptr_t>(newer.wrapper_generation) ? "SAME" : "DIFFERENT");
    std::printf("split inflight reload bridge     old=0x%016" PRIxPTR " new=0x%016" PRIxPTR " %s\n",
                bridge_invoker, newer.invoker_a, bridge_invoker == newer.invoker_a ? "SAME" : "DIFFERENT");
    if (wrapper_probe == reinterpret_cast<uintptr_t>(newer.wrapper_generation)) return 77;
    if (bridge_invoker != newer.invoker_a) return 78;
    if (newer.wrapper_generation() != 1001) return 79;

    newer.register_a(host_a);
    const int fresh = reinterpret_cast<LinkedHostFn>(host_a)(9);
    std::printf("split inflight fresh generation rv=%d want=%d\n", fresh, want_fresh);
    if (fresh != want_fresh) return 80;
    if (dlclose(newer.h) != 0) return 81;
    munmap(reservation, reservation_len);
    std::printf("SPLIT_INFLIGHT_RESULT selected-resident-bridge-survived-wrapper-unmap\n");
    return 0;
  }

'''
    s = replace_once(s, anchor, block + anchor, 'generation loop')
    p.write_text(s)
    print('Patched split fixture with --split-inflight mode')


if __name__ == '__main__':
    main()
