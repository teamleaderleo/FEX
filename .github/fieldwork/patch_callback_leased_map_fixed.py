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

    once(race, "#include <signal.h>\n", "#include <signal.h>\n#include <sys/mman.h>\n", "mmap include")
    once(race, "#include <chrono>\n", "#include <cerrno>\n#include <chrono>\n", "errno include")

    once(
        race,
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
  if (page_size_long <= 0) {
    std::fprintf(stderr, "LEASE_MAP_FIXED bad-page-size\\n");
    return 60;
  }
  const uintptr_t page_size = static_cast<uintptr_t>(page_size_long);
  const uintptr_t replace_page = target & ~(page_size - 1);
  std::atomic<bool> replace_done {false};
  std::atomic<int> replace_errno {-999};
  std::atomic<uintptr_t> replace_rv {0};
  std::fprintf(stderr, "LEASE_MAP_FIXED start page=%#lx target=%#lx unpacker=%#lx\\n",
               replace_page, target, unpacker);

  std::thread replacer([&]() {
    errno = 0;
    void* rv = mmap(reinterpret_cast<void*>(replace_page), page_size,
                    PROT_READ | PROT_WRITE | PROT_EXEC,
                    MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
    const int err = (rv == MAP_FAILED) ? errno : 0;
    replace_rv.store(reinterpret_cast<uintptr_t>(rv), std::memory_order_release);
    replace_errno.store(err, std::memory_order_release);
    replace_done.store(true, std::memory_order_release);
    std::fprintf(stderr, "LEASE_MAP_FIXED returned rv=%p errno=%d\\n", rv, err);
  });

  std::this_thread::sleep_for(std::chrono::milliseconds(300));
  const bool replace_done_before_release = replace_done.load(std::memory_order_acquire);
  std::fprintf(stderr, "LEASE_MAP_FIXED done-before-release=%d rv=%#lx errno=%d\\n",
               replace_done_before_release ? 1 : 0,
               replace_rv.load(std::memory_order_acquire),
               replace_errno.load(std::memory_order_acquire));

  const char release_byte = 'R';
''',
        "insert destructive replacement while lease active",
    )

    once(
        race,
        '''  worker.join();
  closer.join();

  const int worker_rv = worker_result.load(std::memory_order_acquire);
''',
        '''  worker.join();
  closer.join();
  replacer.join();
  std::fprintf(stderr, "LEASE_MAP_FIXED joined done=%d rv=%#lx errno=%d\\n",
               replace_done.load(std::memory_order_acquire) ? 1 : 0,
               replace_rv.load(std::memory_order_acquire),
               replace_errno.load(std::memory_order_acquire));

  const int worker_rv = worker_result.load(std::memory_order_acquire);
''',
        "join replacement thread",
    )

    print(race)


if __name__ == "__main__":
    main()
