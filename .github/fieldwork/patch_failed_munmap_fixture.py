#!/usr/bin/env python3
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FIXTURE_ROOT")
    root = Path(sys.argv[1]).resolve() / "fex-full-thunk-pair"
    p = root / "guest/main.cpp"
    s = p.read_text()

    s = s.replace('  bool pin = false;\n  int cycles = 1;', '  bool pin = false;\n  bool failed_munmap = false;\n  int cycles = 1;', 1)
    s = s.replace(
        '    else if (!std::strcmp(argv[i], "--pin")) pin = true;\n',
        '    else if (!std::strcmp(argv[i], "--pin")) pin = true;\n    else if (!std::strcmp(argv[i], "--failed-munmap")) failed_munmap = true;\n',
        1,
    )

    anchor = '  if (!host_a || host_a != host_b) return 3;\n\n'
    block = r'''  if (!host_a || host_a != host_b) return 3;

  if (failed_munmap) {
    DSO d = load_guest(1);
    d.register_a(host_a);
    const int before = reinterpret_cast<LinkedHostFn>(host_a)(5);
    const int want_before = (5 * 3 + 7) + 1000 + 1;
    std::printf("failed-munmap pre-call           rv=%d want=%d\n", before, want_before);
    if (before != want_before) return 60;

    const long page_size = sysconf(_SC_PAGESIZE);
    if (page_size <= 0) return 61;
    const uintptr_t page = d.invoker_a & ~static_cast<uintptr_t>(page_size - 1);
    errno = 0;
    const int unmap_rc = munmap(reinterpret_cast<void*>(page + 1), static_cast<size_t>(page_size));
    const int unmap_errno = errno;
    std::printf("failed-munmap syscall            rc=%d errno=%d (%s)\n", unmap_rc, unmap_errno, std::strerror(unmap_errno));
    print_mapping("failed-munmap invoker remains", d.invoker_a);
    if (unmap_rc != -1 || unmap_errno != EINVAL || !executable(d.invoker_a)) return 62;

    const int stale_rc = child("Link after failed munmap", [&] {
      return reinterpret_cast<LinkedHostFn>(host_a)(6);
    });
    std::printf("failed-munmap child status       %d\n", stale_rc);

    if (dlclose(d.h) != 0) return 63;
    // Record the policy outcome rather than making the fixture itself choose it.
    // Baseline/prevalidated FEX should report child status 0; premature retirement
    // should report a signal-derived status such as 139.
    return 0;
  }

'''
    if anchor not in s:
        raise SystemExit('failed-munmap insertion anchor missing')
    p.write_text(s.replace(anchor, block, 1))
    print('Patched full-thunk fixture with --failed-munmap')


if __name__ == '__main__':
    main()
