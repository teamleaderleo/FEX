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
        '''  const int protect_rv = mprotect(reinterpret_cast<void*>(target_page), page_size, PROT_READ);
  const int protect_errno = protect_rv == -1 ? errno : 0;
  std::fprintf(stderr,
               "LEASE_MPROTECT returned rv=%d errno=%d target-executable=%d unpacker-executable=%d\\n",
               protect_rv, protect_errno,
               executable_mapping_contains(target) ? 1 : 0,
               executable_mapping_contains(unpacker) ? 1 : 0);

  const char release_byte = 'R';
''',
        '''  const int protect_rv = mprotect(reinterpret_cast<void*>(target_page), page_size, PROT_READ);
  const int protect_errno = protect_rv == -1 ? errno : 0;
  const bool target_exec_after_reject = executable_mapping_contains(target);
  const bool unpacker_exec_after_reject = executable_mapping_contains(unpacker);
  std::fprintf(stderr,
               "LEASE_MPROTECT returned rv=%d errno=%d target-executable=%d unpacker-executable=%d\\n",
               protect_rv, protect_errno,
               target_exec_after_reject ? 1 : 0,
               unpacker_exec_after_reject ? 1 : 0);
  if (protect_rv != -1 || protect_errno != EBUSY || !target_exec_after_reject) {
    std::fprintf(stderr, "LEASE_MPROTECT REJECT_EXPECTATION_FAILED\\n");
    return 63;
  }
  std::fprintf(stderr, "LEASE_MPROTECT ACTIVE_REJECT_OK\\n");

  const char release_byte = 'R';
''',
        "leased mprotect rejection assertion",
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

  void* control = mmap(nullptr, page_size, PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (control == MAP_FAILED) { perror("control mmap"); return 64; }
  if (mprotect(control, page_size, PROT_READ | PROT_EXEC) != 0) {
    perror("control make executable"); return 65;
  }
  errno = 0;
  const int control_rv = mprotect(control, page_size, PROT_READ);
  const int control_errno = control_rv == -1 ? errno : 0;
  std::fprintf(stderr, "LEASE_MPROTECT control rv=%d errno=%d\\n", control_rv, control_errno);
  if (control_rv != 0) return 66;
  if (munmap(control, page_size) != 0) { perror("control munmap"); return 67; }
  std::fprintf(stderr, "LEASE_MPROTECT UNLEASED_CONTROL_OK\\n");

  const int stale_rc = child("stale-first-callback", [&]() { return call_first_callback(6); });
''',
        "unleased mprotect control",
    )

    print(p)


if __name__ == "__main__":
    main()
