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
    once(p, "#include <signal.h>\n", "#include <signal.h>\n#include <sys/mman.h>\n", "mmap include")
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
  const uintptr_t source_page = target & ~(page_size - 1);
  void* reserve = mmap(nullptr, page_size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (reserve == MAP_FAILED) { perror("mmap reserve"); return 61; }
  const uintptr_t destination_page = reinterpret_cast<uintptr_t>(reserve);
  if (munmap(reserve, page_size) != 0) { perror("munmap reserve"); return 62; }

  errno = 0;
  std::fprintf(stderr, "LEASE_MREMAP start source=%#lx destination=%#lx target=%#lx unpacker=%#lx\\n",
               source_page, destination_page, target, unpacker);
  void* moved = mremap(reinterpret_cast<void*>(source_page), page_size, page_size,
                       MREMAP_MAYMOVE | MREMAP_FIXED,
                       reinterpret_cast<void*>(destination_page));
  const int move_errno = (moved == MAP_FAILED) ? errno : 0;
  std::fprintf(stderr,
               "LEASE_MREMAP returned rv=%p errno=%d source-mapped=%d destination-mapped=%d\\n",
               moved, move_errno,
               executable_mapping_contains(source_page) ? 1 : 0,
               executable_mapping_contains(destination_page) ? 1 : 0);

  const char release_byte = 'R';
''',
        "forced mremap before callback release",
    )
    print(p)


if __name__ == "__main__":
    main()
