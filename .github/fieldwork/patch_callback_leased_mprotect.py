#!/usr/bin/env python3
from pathlib import Path
import sys


def once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor, found {n}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_FULL_THUNK_PAIR_DIR")
    p = Path(sys.argv[1]).resolve() / "guest/callback_inflight.cpp"

    once(p, "#include <signal.h>\n", "#include <signal.h>\n#include <sys/mman.h>\n", "mman include")
    once(p, "#include <chrono>\n", "#include <cerrno>\n#include <chrono>\n", "errno include")

    once(
        p,
        '''  std::fprintf(stderr,
               "INFLIGHT close-done-before-release=%d target-mapped-before-release=%d unpacker-mapped-before-release=%d\\n",
               done_before_release ? 1 : 0,
               target_mapped_before_release ? 1 : 0,
               unpacker_mapped_before_release ? 1 : 0);

  const char release_byte = 'R';
''',
        '''  std::fprintf(stderr,
               "INFLIGHT close-done-before-release=%d target-mapped-before-release=%d unpacker-mapped-before-release=%d\\n",
               done_before_release ? 1 : 0,
               target_mapped_before_release ? 1 : 0,
               unpacker_mapped_before_release ? 1 : 0);

  const long page_size_long = sysconf(_SC_PAGESIZE);
  if (page_size_long <= 0) return 60;
  const uintptr_t page_size = static_cast<uintptr_t>(page_size_long);
  const uintptr_t target_page = target & ~(page_size - 1);
  errno = 0;
  std::fprintf(stderr, "LEASE_MPROTECT start page=%#lx target=%#lx unpacker=%#lx\\n",
               target_page, target, unpacker);
  const int protect_rv = mprotect(reinterpret_cast<void*>(target_page), page_size, PROT_READ);
  const int protect_errno = protect_rv == -1 ? errno : 0;
  std::fprintf(stderr,
               "LEASE_MPROTECT returned rv=%d errno=%d target-executable=%d unpacker-executable=%d\\n",
               protect_rv, protect_errno,
               executable_mapping_contains(target) ? 1 : 0,
               executable_mapping_contains(unpacker) ? 1 : 0);

  const char release_byte = 'R';
''',
        "remove execute permission before release",
    )

    print(p)


if __name__ == "__main__":
    main()
