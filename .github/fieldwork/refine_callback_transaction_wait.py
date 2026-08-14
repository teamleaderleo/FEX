#!/usr/bin/env python3
from pathlib import Path
import sys


def once(path: Path, old: str, new: str, label: str) -> None:
    s = path.read_text()
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor, found {n}")
    path.write_text(s.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} FEX_SOURCE_ROOT")
    p = Path(sys.argv[1]).resolve() / "Source/Tools/LinuxEmulation/Thunks.cpp"

    once(
        p,
        '''  bool TryAcquire() {
    std::lock_guard lk(Mutex);
    if (Status != State::Live) {
      return false;
    }
    ++Active;
    return true;
  }
''',
        '''  bool TryAcquire() {
    std::unique_lock lk(Mutex);
    CV.wait(lk, [this]() { return Status != State::Draining; });
    if (Status != State::Live) {
      return false;
    }
    ++Active;
    return true;
  }
''',
        "wait while descriptor transaction is draining",
    )

    once(
        p,
        '''  void CommitRevoke() {
    std::lock_guard lk(Mutex);
    Status = State::Revoked;
    DrainRequests = 0;
  }
''',
        '''  void CommitRevoke() {
    std::lock_guard lk(Mutex);
    Status = State::Revoked;
    DrainRequests = 0;
    CV.notify_all();
  }
''',
        "wake acquisition waiters on commit",
    )

    once(
        p,
        '''  void RollbackDrain() {
    std::lock_guard lk(Mutex);
    LOGMAN_THROW_A_FMT(DrainRequests != 0, "Callback descriptor drain-request underflow");
    --DrainRequests;
    if (DrainRequests == 0 && Status == State::Draining) {
      Status = State::Live;
    }
  }
''',
        '''  void RollbackDrain() {
    std::lock_guard lk(Mutex);
    // Another overlapping successful retirement may already have permanently
    // revoked this descriptor. In that case this transaction has nothing left
    // to roll back and must not underflow the request count.
    if (Status == State::Revoked) {
      return;
    }
    LOGMAN_THROW_A_FMT(DrainRequests != 0, "Callback descriptor drain-request underflow");
    --DrainRequests;
    if (DrainRequests == 0 && Status == State::Draining) {
      Status = State::Live;
      CV.notify_all();
    }
  }
''',
        "wake acquisition waiters on rollback",
    )

    print("Refined callback transaction: wait-on-Draining and overlap-safe rollback")


if __name__ == "__main__":
    main()
