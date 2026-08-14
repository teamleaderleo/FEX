#!/usr/bin/env python3
"""Select a real-FEX CustomIR retirement ablation after applying v2 + repair.

Modes:
  full          retire registry H->T and exact-evict translated/cache H
  registry-only retire registry H->T but intentionally leave translated/cache H
  cache-only    keep registry H->T but exact-evict translated/cache H

This is internal causal instrumentation, not a production patch.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    p = ROOT / path
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one ablation anchor, found {count}")
    p.write_text(text.replace(old, new, 1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("full", "registry-only", "cache-only"))
    args = parser.parse_args()

    if args.mode == "registry-only":
        replace_once(
            "Source/Tools/LinuxEmulation/LinuxSyscalls/ThreadManager.h",
            """    for (const auto Entrypoint : Entrypoints) {
      CTX->InvalidateCodeBuffersCodeEntry(Entrypoint);
      for (auto& Thread : Threads) {
        CTX->InvalidateThreadCachedCodeEntry(Thread->Thread, Entrypoint);
      }
    }
""",
            """    // ABLATION registry-only: intentionally preserve translated/cache H
    // after registry retirement to test whether pretranslated state alone can
    // retain the stale generation.
    (void)Entrypoints;
""",
        )
    elif args.mode == "cache-only":
        replace_once(
            "FEXCore/Source/Interface/Core/Core.cpp",
            """        RetiredEntrypoints.emplace_back(it->first);
        it = CustomIRHandlers.erase(it);
        continue;
""",
            """        RetiredEntrypoints.emplace_back(it->first);
        // ABLATION cache-only: intentionally keep the H->T registry owner while
        // returning H for exact translated/cache eviction.
        ++it;
        continue;
""",
        )

    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    print(f"exact CustomIR retirement ablation mode: {args.mode}")


if __name__ == "__main__":
    main()
