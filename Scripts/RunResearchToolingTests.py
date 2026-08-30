#!/usr/bin/env python3
"""Run only the owned fork's small research-tooling tests.

This deliberately does not initialize submodules, configure CMake, compile FEX,
or run product tests. Independent test files run concurrently, while their
captured logs are printed in a stable order after every worker finishes.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Sequence


TEST_FILES = (
    "Scripts/test_fork_workflow_registry.py",
    "Scripts/test_owned_fork_ci_policy.py",
    "Scripts/test_research_dev_build.py",
    "Scripts/test_research_profile_carrier.py",
    "Scripts/test_research_tooling_runner.py",
    "Scripts/test_submodule_origin_cache.py",
    "Scripts/test_submodule_pack_cache.py",
)
MAX_JOBS = len(TEST_FILES)


@dataclasses.dataclass(frozen=True)
class TestResult:
    path: str
    returncode: int
    duration_seconds: float
    output: str


def jobs_argument(value: str) -> int:
    try:
        jobs = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("jobs must be an integer") from exc
    if not 1 <= jobs <= MAX_JOBS:
        raise argparse.ArgumentTypeError(f"jobs must be between 1 and {MAX_JOBS}")
    return jobs


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--jobs",
        type=jobs_argument,
        default=min(4, MAX_JOBS),
        help=f"independent test-file workers (1-{MAX_JOBS}; default: 4)",
    )
    result.add_argument(
        "--list",
        action="store_true",
        help="print the closed test inventory without running it",
    )
    return result


def validate_inventory(repo_root: Path, test_files: Sequence[str]) -> None:
    if len(test_files) != len(set(test_files)):
        raise RuntimeError("research-tooling test inventory contains duplicates")
    for relative in test_files:
        path = repo_root / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or path.parent != repo_root / "Scripts"
            or not path.name.startswith("test_")
            or path.suffix != ".py"
        ):
            raise RuntimeError(f"unsafe or missing research-tooling test: {relative}")


def run_one(
    repo_root: Path,
    relative: str,
    *,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
) -> TestResult:
    started = clock()
    completed = process_runner(
        [sys.executable, relative],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return TestResult(
        path=relative,
        returncode=completed.returncode,
        duration_seconds=clock() - started,
        output=completed.stdout,
    )


def run_inventory(
    repo_root: Path,
    test_files: Sequence[str],
    jobs: int,
    *,
    worker: Callable[[Path, str], TestResult] = run_one,
) -> list[TestResult]:
    validate_inventory(repo_root, test_files)
    indexed: dict[str, TestResult] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {
            executor.submit(worker, repo_root, relative): relative
            for relative in test_files
        }
        for future in concurrent.futures.as_completed(futures):
            relative = futures[future]
            indexed[relative] = future.result()
    return [indexed[relative] for relative in test_files]


def emit_results(results: Sequence[TestResult], elapsed: float) -> int:
    failed = 0
    for result in results:
        state = "PASS" if result.returncode == 0 else "FAIL"
        print(f"=== {state} {result.path} ({result.duration_seconds:.3f}s) ===")
        if result.output:
            print(result.output, end="" if result.output.endswith("\n") else "\n")
        failed += result.returncode != 0
    print(
        json.dumps(
            {
                "format": "teamleaderleo-fex-research-tooling-tests-v1",
                "files": len(results),
                "failed": failed,
                "elapsedSeconds": round(elapsed, 6),
                "status": "pass" if failed == 0 else "fail",
            },
            sort_keys=True,
        )
    )
    return 0 if failed == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    validate_inventory(repo_root, TEST_FILES)
    if args.list:
        print("\n".join(TEST_FILES))
        return 0
    started = time.monotonic()
    results = run_inventory(repo_root, TEST_FILES, args.jobs)
    return emit_results(results, time.monotonic() - started)


if __name__ == "__main__":
    raise SystemExit(main())
