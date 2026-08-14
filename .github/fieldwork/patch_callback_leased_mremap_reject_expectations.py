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

    once(
        p,
        '''  std::fprintf(stderr,
               "LEASE_MREMAP returned rv=%p errno=%d source-mapped=%d destination-mapped=%d\\n",
               moved, move_errno,
               executable_mapping_contains(source_page) ? 1 : 0,
               executable_mapping_contains(destination_page) ? 1 : 0);

  const char release_byte = 'R';
''',
        '''  const bool source_mapped_after_reject = executable_mapping_contains(source_page);
  const bool destination_mapped_after_reject = executable_mapping_contains(destination_page);
  std::fprintf(stderr,
               "LEASE_MREMAP returned rv=%p errno=%d source-mapped=%d destination-mapped=%d\\n",
               moved, move_errno,
               source_mapped_after_reject ? 1 : 0,
               destination_mapped_after_reject ? 1 : 0);
  if (moved != MAP_FAILED || move_errno != EBUSY || !source_mapped_after_reject || destination_mapped_after_reject) {
    std::fprintf(stderr, "LEASE_MREMAP REJECT_EXPECTATION_FAILED\\n");
    return 63;
  }
  std::fprintf(stderr, "LEASE_MREMAP ACTIVE_REJECT_OK\\n");

  const char release_byte = 'R';
''',
        "leased mremap rejection assertion",
    )

    once(
        p,
        '''  if (target_mapped_after_release || unpacker_mapped_after_release) {
    std::fprintf(stderr, "INFLIGHT DEFERRED_OWNER_NOT_RECLAIMED\\n");
    return 44;
  }

  const int stale_rc = child("stale-first-callback", [&]() { return call_first_callback(6); });
''',
        '''  if (target_mapped_after_release || unpacker_mapped_after_release) {
    std::fprintf(stderr, "INFLIGHT DEFERRED_OWNER_NOT_RECLAIMED\\n");
    return 44;
  }

  void* control_source = mmap(nullptr, page_size, PROT_READ | PROT_WRITE,
                              MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (control_source == MAP_FAILED) { perror("control source mmap"); return 64; }
  void* control_reserve = mmap(nullptr, page_size, PROT_NONE,
                               MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (control_reserve == MAP_FAILED) { perror("control reserve mmap"); return 65; }
  const uintptr_t control_destination = reinterpret_cast<uintptr_t>(control_reserve);
  if (munmap(control_reserve, page_size) != 0) { perror("control reserve munmap"); return 66; }
  errno = 0;
  void* control_moved = mremap(control_source, page_size, page_size,
                               MREMAP_MAYMOVE | MREMAP_FIXED,
                               reinterpret_cast<void*>(control_destination));
  const int control_errno = control_moved == MAP_FAILED ? errno : 0;
  std::fprintf(stderr, "LEASE_MREMAP control rv=%p errno=%d destination=%#lx\\n",
               control_moved, control_errno, control_destination);
  if (control_moved == MAP_FAILED || reinterpret_cast<uintptr_t>(control_moved) != control_destination) {
    return 67;
  }
  if (munmap(control_moved, page_size) != 0) { perror("control moved munmap"); return 68; }
  std::fprintf(stderr, "LEASE_MREMAP UNLEASED_CONTROL_OK\\n");

  const int stale_rc = child("stale-first-callback", [&]() { return call_first_callback(6); });
''',
        "unleased mremap control",
    )

    print(p)


if __name__ == "__main__":
    main()
