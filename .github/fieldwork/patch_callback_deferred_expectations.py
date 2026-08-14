#!/usr/bin/env python3
from pathlib import Path
import sys


def once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_FULL_THUNK_PAIR_DIR")
    root = Path(sys.argv[1]).resolve()
    race = root / "guest/callback_inflight.cpp"

    once(
        race,
        "#include <cstdio>\n",
        "#include <cstdio>\n#include <cstdint>\n",
        "cstdint include",
    )

    once(
        race,
        "int main() {\n",
        r'''static bool executable_mapping_contains(uintptr_t address) {
  FILE* maps = std::fopen("/proc/self/maps", "r");
  if (!maps) {
    return false;
  }

  char line[512];
  while (std::fgets(line, sizeof(line), maps)) {
    unsigned long long start = 0;
    unsigned long long end = 0;
    char perms[5] {};
    if (std::sscanf(line, "%llx-%llx %4s", &start, &end, perms) != 3) {
      continue;
    }
    if (address >= start && address < end) {
      std::fclose(maps);
      return std::strchr(perms, 'x') != nullptr;
    }
  }
  std::fclose(maps);
  return false;
}

int main() {
''',
        "mapping helper",
    )

    once(
        race,
        '''  const bool done_before_release = close_done.load(std::memory_order_acquire);
  std::fprintf(stderr, "INFLIGHT close-done-before-release=%d\\n", done_before_release ? 1 : 0);

  const char release_byte = 'R';
''',
        '''  const bool done_before_release = close_done.load(std::memory_order_acquire);
  const bool target_mapped_before_release = executable_mapping_contains(target);
  const bool unpacker_mapped_before_release = executable_mapping_contains(unpacker);
  std::fprintf(stderr,
               "INFLIGHT close-done-before-release=%d target-mapped-before-release=%d unpacker-mapped-before-release=%d\\n",
               done_before_release ? 1 : 0,
               target_mapped_before_release ? 1 : 0,
               unpacker_mapped_before_release ? 1 : 0);

  const char release_byte = 'R';
''',
        "pre-release mapping observation",
    )

    once(
        race,
        '''  // With a real drain, dlclose must still be blocked before release, the callback
  // must complete normally, and the escaped old trampoline must be revoked.
  if (done_before_release) {
    std::fprintf(stderr, "INFLIGHT UNSAFE_CLOSE_WON_RACE\\n");
    return 40;
  }
  if (worker_rv != 70053 || close_rv != 0) {
''',
        '''  // Deferred reclamation deliberately lets guest dlclose complete while the
  // physical executable mapping remains pinned by the active callback lease.
  if (!done_before_release) {
    std::fprintf(stderr, "INFLIGHT DEFERRED_CLOSE_DID_NOT_RETURN\\n");
    return 40;
  }
  if (!target_mapped_before_release || !unpacker_mapped_before_release) {
    std::fprintf(stderr, "INFLIGHT OWNER_NOT_PINNED_BEFORE_RELEASE target=%d unpacker=%d\\n",
                 target_mapped_before_release ? 1 : 0,
                 unpacker_mapped_before_release ? 1 : 0);
    return 43;
  }
  if (worker_rv != 70053 || close_rv != 0) {
''',
        "nonblocking close expectation",
    )

    once(
        race,
        '''  const int stale_rc = child("stale-first-callback", [&]() { return call_first_callback(6); });
  if (stale_rc != 113) {
''',
        '''  const bool target_mapped_after_release = executable_mapping_contains(target);
  const bool unpacker_mapped_after_release = executable_mapping_contains(unpacker);
  std::fprintf(stderr,
               "INFLIGHT mapped-after-release target=%d unpacker=%d\\n",
               target_mapped_after_release ? 1 : 0,
               unpacker_mapped_after_release ? 1 : 0);
  if (target_mapped_after_release || unpacker_mapped_after_release) {
    std::fprintf(stderr, "INFLIGHT DEFERRED_OWNER_NOT_RECLAIMED\\n");
    return 44;
  }

  const int stale_rc = child("stale-first-callback", [&]() { return call_first_callback(6); });
  if (stale_rc != 113) {
''',
        "post-release mapping observation",
    )

    once(
        race,
        '  std::fprintf(stderr, "INFLIGHT DRAIN_PASS\\n");\n',
        '  std::fprintf(stderr, "INFLIGHT DEFERRED_LEASE_PASS\\n");\n',
        "result marker",
    )

    print(race)


if __name__ == "__main__":
    main()
