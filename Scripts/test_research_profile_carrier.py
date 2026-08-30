#!/usr/bin/env python3
"""Focused tests for the checked-in research-profile carrier."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import ResearchProfileCarrier as carrier


class ResearchProfileCarrierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.profile = self.source / "Scripts" / "ResearchProfiles" / "sample"
        self.profile.mkdir(parents=True)
        self.write_manifest()
        self.write_script(
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' '{"schemaVersion":1,"status":"pass","summary":"focused synthetic pass"}' \\
  > "${FEX_RESEARCH_RECEIPTS}/profile-outcome.json"
"""
        )
        subprocess.run(["git", "init", "-q", self.source], check=True)
        subprocess.run(["git", "-C", self.source, "config", "user.name", "Test"], check=True)
        subprocess.run(
            ["git", "-C", self.source, "config", "user.email", "test@example.invalid"],
            check=True,
        )
        subprocess.run(["git", "-C", self.source, "add", "."], check=True)
        subprocess.run(["git", "-C", self.source, "commit", "-qm", "fixture"], check=True)
        self.source_sha = self.git("rev-parse", "HEAD").strip()

    def git(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", self.source, *arguments],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout

    def write_manifest(self, **overrides: object) -> None:
        manifest: dict[str, object] = {
            "schemaVersion": 1,
            "id": "sample",
            "title": "Synthetic profile",
            "entrypoint": "run.sh",
            "platform": "ubuntu-24.04-arm",
            "timeoutSeconds": 10,
            "variants": ["default", "negative"],
        }
        manifest.update(overrides)
        (self.profile / "profile.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
        )

    def write_script(self, text: str) -> None:
        (self.profile / "run.sh").write_text(text, encoding="utf-8")

    def run_carrier(self, receipts: Path | None = None, **overrides: object) -> int:
        arguments = {
            "source": self.source,
            "source_sha": self.source_sha,
            "carrier_sha": "1" * 40,
            "profile": "sample",
            "variant": "default",
            "platform": "ubuntu-24.04-arm",
            "jobs": 4,
            "receipts": receipts or (self.root / "receipts"),
        }
        arguments.update(overrides)
        argv = ["run"]
        for key, value in arguments.items():
            argv.extend((f"--{key.replace('_', '-')}", str(value)))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return carrier.main(argv)

    def recommit(self) -> None:
        subprocess.run(["git", "-C", self.source, "add", "."], check=True)
        subprocess.run(["git", "-C", self.source, "commit", "-qm", "update"], check=True)
        self.source_sha = self.git("rev-parse", "HEAD").strip()

    def test_checked_in_profile_passes_and_binds_both_heads(self) -> None:
        receipts = self.root / "pass-receipts"
        self.assertEqual(self.run_carrier(receipts), 0)
        result = json.loads((receipts / "carrier-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["sourceSha"], self.source_sha)
        self.assertEqual(result["carrierSha"], "1" * 40)
        self.assertEqual(result["sourceStateBefore"], result["sourceStateAfter"])
        self.assertEqual(result["profileOutcome"]["status"], "pass")

    def test_x86_profile_passes_on_the_x86_runner_adapter(self) -> None:
        self.write_manifest(platform="self-hosted-x86-fex-research")
        self.recommit()
        self.assertEqual(self.run_carrier(platform="self-hosted-x86-fex-research"), 0)

    def test_profile_platform_mismatch_is_rejected(self) -> None:
        self.assertEqual(self.run_carrier(platform="self-hosted-x86-fex-research"), 2)

    def test_unsupported_manifest_platform_is_rejected(self) -> None:
        self.write_manifest(platform="ubuntu-latest")
        self.recommit()
        self.assertEqual(self.run_carrier(), 2)

    def test_profile_and_variant_traversal_are_rejected(self) -> None:
        self.assertEqual(self.run_carrier(profile="../sample"), 2)
        self.assertEqual(self.run_carrier(variant="../default"), 2)

    def test_undeclared_variant_is_rejected(self) -> None:
        self.assertEqual(self.run_carrier(variant="missing"), 2)

    def test_manifest_unknown_key_is_rejected(self) -> None:
        self.write_manifest(command="arbitrary shell")
        self.recommit()
        self.assertEqual(self.run_carrier(), 2)

    def test_uncommitted_profile_is_rejected(self) -> None:
        uncommitted = self.source / "Scripts" / "ResearchProfiles" / "uncommitted"
        uncommitted.mkdir()
        manifest = json.loads((self.profile / "profile.json").read_text(encoding="utf-8"))
        manifest["id"] = "uncommitted"
        (uncommitted / "profile.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
        )
        (uncommitted / "run.sh").write_text(
            (self.profile / "run.sh").read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.assertEqual(self.run_carrier(profile="uncommitted"), 2)

    def test_symlinked_manifest_is_rejected(self) -> None:
        external = self.root / "external.json"
        external.write_text("{}\n", encoding="utf-8")
        (self.profile / "profile.json").unlink()
        (self.profile / "profile.json").symlink_to(external)
        self.recommit()
        self.assertEqual(self.run_carrier(), 2)

    def test_symlinked_entrypoint_is_rejected(self) -> None:
        external = self.root / "external.sh"
        external.write_text("exit 0\n", encoding="utf-8")
        (self.profile / "run.sh").unlink()
        (self.profile / "run.sh").symlink_to(external)
        self.recommit()
        self.assertEqual(self.run_carrier(), 2)

    def test_zero_exit_without_outcome_is_not_a_pass(self) -> None:
        self.write_script("#!/usr/bin/env bash\nexit 0\n")
        self.recommit()
        receipts = self.root / "missing-outcome"
        self.assertEqual(self.run_carrier(receipts), 1)
        result = json.loads((receipts / "carrier-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "invalid-outcome")

    def test_nonzero_profile_is_recorded_as_failure(self) -> None:
        self.write_script("#!/usr/bin/env bash\nexit 17\n")
        self.recommit()
        receipts = self.root / "failed-profile"
        self.assertEqual(self.run_carrier(receipts), 1)
        result = json.loads((receipts / "carrier-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "profile-failed")
        self.assertEqual(result["profileExitCode"], 17)

    def test_tracked_source_mutation_invalidates_the_run(self) -> None:
        tracked = self.source / "tracked.txt"
        tracked.write_text("before\n", encoding="utf-8")
        self.write_script(
            """#!/usr/bin/env bash
set -euo pipefail
printf 'after\n' >> "${FEX_RESEARCH_SOURCE}/tracked.txt"
printf '%s\n' '{"schemaVersion":1,"status":"pass","summary":"should be invalid"}' \\
  > "${FEX_RESEARCH_RECEIPTS}/profile-outcome.json"
"""
        )
        self.recommit()
        receipts = self.root / "mutated-source"
        self.assertEqual(self.run_carrier(receipts), 1)
        result = json.loads((receipts / "carrier-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "invalid-source-after-run")
        self.assertIsNone(result["sourceStateAfter"])

    def test_wrong_source_sha_is_rejected_before_execution(self) -> None:
        self.assertEqual(self.run_carrier(source_sha="2" * 40), 2)

    def test_timeout_terminates_the_profile_process_group(self) -> None:
        self.write_manifest(timeoutSeconds=1)
        self.write_script("#!/usr/bin/env bash\nset -euo pipefail\nsleep 30\n")
        self.recommit()
        receipts = self.root / "timed-out"
        self.assertEqual(self.run_carrier(receipts), 1)
        result = json.loads((receipts / "carrier-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "profile-timeout")
        self.assertIn("process group was terminated", result["error"])

    def test_nonempty_receipt_directory_is_rejected(self) -> None:
        receipts = self.root / "occupied"
        receipts.mkdir()
        (receipts / "foreign.txt").write_text("keep\n", encoding="utf-8")
        self.assertEqual(self.run_carrier(receipts), 2)
        self.assertEqual((receipts / "foreign.txt").read_text(encoding="utf-8"), "keep\n")

    def test_symlinked_receipt_directory_is_rejected(self) -> None:
        external = self.root / "external-receipts"
        external.mkdir()
        receipts = self.root / "receipt-link"
        receipts.symlink_to(external, target_is_directory=True)
        self.assertEqual(self.run_carrier(receipts), 2)
        self.assertEqual(list(external.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
