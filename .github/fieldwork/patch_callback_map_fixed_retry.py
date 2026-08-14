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
    race = Path(sys.argv[1]).resolve() / "guest/callback_inflight.cpp"

    once(
        race,
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

  errno = 0;
  void* retry = mmap(reinterpret_cast<void*>(replace_page), page_size,
                     PROT_READ | PROT_WRITE | PROT_EXEC,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
  const int retry_errno = (retry == MAP_FAILED) ? errno : 0;
  std::fprintf(stderr, "LEASE_MAP_FIXED retry-after-release rv=%p errno=%d mapped=%d\\n",
               retry, retry_errno, executable_mapping_contains(replace_page) ? 1 : 0);
  if (retry == MAP_FAILED || retry != reinterpret_cast<void*>(replace_page)) {
    return 61;
  }

  const int stale_rc = child("stale-first-callback", [&]() { return call_first_callback(6); });
''',
        "retry destructive replacement after final lease release",
    )

    print(race)


if __name__ == "__main__":
    main()
