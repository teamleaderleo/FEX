#!/usr/bin/env python3
"""Focused tests for the bounded research-tooling test runner."""

from __future__ import annotations

import argparse
import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

import RunResearchToolingTests as runner


class ResearchToolingRunnerTest(unittest.TestCase):
    def test_inventory_is_the_complete_closed_test_file_set(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        discovered = tuple(
            f"Scripts/{path.name}"
            for path in sorted((repo_root / "Scripts").glob("test_*.py"))
        )
        self.assertEqual(discovered, runner.TEST_FILES)
        runner.validate_inventory(repo_root, runner.TEST_FILES)

    def test_results_keep_inventory_order_and_aggregate_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / "Scripts"
            scripts.mkdir()
            inventory = ("Scripts/test_z.py", "Scripts/test_a.py")
            for relative in inventory:
                (root / relative).write_text("# fixture\n", encoding="utf-8")

            def worker(_root: Path, relative: str) -> runner.TestResult:
                return runner.TestResult(
                    path=relative,
                    returncode=1 if relative.endswith("a.py") else 0,
                    duration_seconds=0.25,
                    output=f"output:{relative}\n",
                )

            results = runner.run_inventory(root, inventory, 2, worker=worker)
            self.assertEqual(list(inventory), [result.path for result in results])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = runner.emit_results(results, 0.5)
            self.assertEqual(1, exit_code)
            self.assertLess(
                output.getvalue().index("test_z.py"),
                output.getvalue().index("test_a.py"),
            )
            self.assertIn('"failed": 1', output.getvalue())

    def test_run_one_uses_python_without_a_shell_and_captures_output(self) -> None:
        calls: list[tuple[object, dict[str, object]]] = []

        def process(command: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 3, "synthetic failure\n")

        ticks = iter((10.0, 10.125))
        result = runner.run_one(
            Path("/repo"),
            "Scripts/test_sample.py",
            process_runner=process,
            clock=lambda: next(ticks),
        )
        self.assertEqual(3, result.returncode)
        self.assertEqual(0.125, result.duration_seconds)
        self.assertEqual("synthetic failure\n", result.output)
        command, options = calls[0]
        self.assertEqual([runner.sys.executable, "Scripts/test_sample.py"], command)
        self.assertNotIn("shell", options)
        self.assertFalse(options["check"])

    def test_jobs_are_strictly_bounded(self) -> None:
        self.assertEqual(1, runner.jobs_argument("1"))
        self.assertEqual(runner.MAX_JOBS, runner.jobs_argument(str(runner.MAX_JOBS)))
        for invalid in ("0", str(runner.MAX_JOBS + 1), "many"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(argparse.ArgumentTypeError):
                    runner.jobs_argument(invalid)


if __name__ == "__main__":
    unittest.main()
