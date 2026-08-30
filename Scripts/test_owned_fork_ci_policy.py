#!/usr/bin/env python3
"""Regression checks for the owned fork's explicit CI scheduling boundary."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BROAD_WORKFLOWS = (
    "ccpp.yml",
    "glibc_fault.yml",
    "hostrunner.yml",
    "instcountci.yml",
    "mingw_build.yml",
    "steamrt4.yml",
    "vixl_simulator.yml",
    "wine_dll_artifacts.yml",
)


class OwnedForkCIPolicyTest(unittest.TestCase):
    def workflow(self, name: str) -> str:
        return (REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_inherited_broad_workflows_are_manual_only(self) -> None:
        for name in BROAD_WORKFLOWS:
            with self.subTest(workflow=name):
                workflow = self.workflow(name)
                self.assertIn("workflow_dispatch:", workflow)
                self.assertNotIn("pull_request:", workflow)
                self.assertNotIn("\n  push:", workflow)
                self.assertNotIn("ci:full", workflow)

    def test_upstream_commenting_formatter_is_not_registered(self) -> None:
        self.assertFalse(
            (REPO_ROOT / ".github" / "workflows" / "pr-code-format.yml").exists()
        )

    def test_x86_lane_is_manual_exact_sha_and_profile_only(self) -> None:
        workflow = self.workflow("focused-x86-research.yml")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn("\n  push:", workflow)
        self.assertIn("runs-on: [self-hosted, X64, fex-research]", workflow)
        self.assertIn("source_sha:", workflow)
        self.assertIn("profile:", workflow)
        self.assertIn("variant:", workflow)
        self.assertNotIn("command:", workflow)
        self.assertNotIn("target:", workflow)
        self.assertIn("ResearchProfileCarrier.py run", workflow)
        self.assertIn("--platform self-hosted-x86-fex-research", workflow)
        self.assertIn("github.workflow_sha", workflow)
        self.assertIn("ref: ${{ github.workflow_sha }}", workflow)
        self.assertIn("ref: ${{ inputs.source_sha }}", workflow)
        self.assertIn('test "${GITHUB_REF}" = "refs/heads/${DEFAULT_BRANCH}"', workflow)
        self.assertIn("persist-credentials: false", workflow)

    def test_custom_runner_label_is_declared_for_static_lint(self) -> None:
        config = (REPO_ROOT / ".github" / "actionlint.yaml").read_text(encoding="utf-8")
        self.assertIn("self-hosted-runner:", config)
        self.assertIn("- fex-research", config)

    def test_focused_lane_actions_are_commit_pinned(self) -> None:
        workflow = self.workflow("focused-x86-research.yml")
        actions = re.findall(r"^\s*uses:\s+([^\s#]+)", workflow, flags=re.MULTILINE)
        self.assertTrue(actions)
        for action in actions:
            with self.subTest(action=action):
                _, separator, revision = action.rpartition("@")
                self.assertEqual(separator, "@")
                self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_compiler_lane_is_same_repo_path_scoped_and_bounded(self) -> None:
        workflow = self.workflow("focused-compiler-compat.yml")
        pull_request_scope = workflow.split("permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertNotIn("\n  push:", workflow)
        self.assertNotIn("- Scripts/ResearchDevBuild.py", pull_request_scope)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", workflow)
        self.assertIn("runs-on: ubuntu-26.04", workflow)
        self.assertIn("clang-21", workflow)
        self.assertIn("CharPointerHostToGuestConversionPreservesAddress.ThunkGen", workflow)
        self.assertIn("CharPointerReturnsUseConvertibleGuestLayout.ThunkGen", workflow)
        self.assertIn("build GL-host-64 --jobs 4", workflow)
        self.assertNotIn("cmake --build build", workflow)

    def test_compiler_lane_actions_are_commit_pinned(self) -> None:
        workflow = self.workflow("focused-compiler-compat.yml")
        actions = re.findall(r"^\s*uses:\s+([^\s#]+)", workflow, flags=re.MULTILINE)
        self.assertTrue(actions)
        for action in actions:
            with self.subTest(action=action):
                _, separator, revision = action.rpartition("@")
                self.assertEqual(separator, "@")
                self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_code_cache_lane_compiles_watched_disk_io_wiring(self) -> None:
        workflow = self.workflow("focused-code-cache.yml")
        pull_request_scope = workflow.split("permissions:", 1)[0]
        self.assertIn("pull_request:", workflow)
        self.assertNotIn("\n  push:", workflow)
        self.assertIn("- FEXCore/Source/Interface/Core/DiskCache.cpp", pull_request_scope)
        self.assertIn("- FEXCore/include/FEXCore/Utils/File.h", pull_request_scope)
        self.assertIn(
            "- FEXCore/unittests/APITests/DiskCacheIndexRecovery.cpp",
            pull_request_scope,
        )
        self.assertIn("build FEXCore_Tests_DiskCacheIndexRecovery --jobs 4", workflow)
        self.assertIn("DiskCacheIndexRecovery", workflow)
        self.assertNotIn("cmake --build", workflow)

    def test_research_tooling_lane_is_path_scoped_and_product_free(self) -> None:
        workflow = self.workflow("research-tooling.yml")
        pull_request_scope = workflow.split("permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertNotIn("\n  push:", workflow)
        self.assertIn("- Scripts/ResearchDevBuild.py", pull_request_scope)
        self.assertIn("- Scripts/test_*.py", pull_request_scope)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            workflow,
        )
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertIn("RunResearchToolingTests.py --jobs 4", workflow)
        for forbidden in (
            "ResearchDevBuild.py submodules",
            "ResearchProfileCarrier.py run",
            "cmake ",
            "ctest ",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, workflow)

    def test_research_tooling_lane_actions_are_commit_pinned(self) -> None:
        workflow = self.workflow("research-tooling.yml")
        actions = re.findall(r"^\s*uses:\s+([^\s#]+)", workflow, flags=re.MULTILINE)
        self.assertTrue(actions)
        for action in actions:
            with self.subTest(action=action):
                _, separator, revision = action.rpartition("@")
                self.assertEqual(separator, "@")
                self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_arm64_lane_is_manual_exact_sha_and_profile_only(self) -> None:
        workflow = self.workflow("focused-arm64-research.yml")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn("\n  push:", workflow)
        self.assertIn("runs-on: ubuntu-24.04-arm", workflow)
        self.assertIn("source_sha:", workflow)
        self.assertIn("profile:", workflow)
        self.assertIn("variant:", workflow)
        self.assertNotIn("command:", workflow)
        self.assertIn("ResearchProfileCarrier.py run", workflow)
        self.assertIn("--platform ubuntu-24.04-arm", workflow)
        self.assertIn("github.workflow_sha", workflow)
        self.assertIn("ref: ${{ github.workflow_sha }}", workflow)
        self.assertIn("ref: ${{ inputs.source_sha }}", workflow)
        self.assertIn('test "${GITHUB_REF}" = "refs/heads/${DEFAULT_BRANCH}"', workflow)
        self.assertIn("persist-credentials: false", workflow)

    def test_arm64_lane_actions_are_commit_pinned(self) -> None:
        workflow = self.workflow("focused-arm64-research.yml")
        actions = re.findall(r"^\s*uses:\s+([^\s#]+)", workflow, flags=re.MULTILINE)
        self.assertTrue(actions)
        for action in actions:
            with self.subTest(action=action):
                _, separator, revision = action.rpartition("@")
                self.assertEqual(separator, "@")
                self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_arm64_compiler_cache_is_bounded_and_profile_scoped(self) -> None:
        workflow = self.workflow("focused-arm64-research.yml")
        job_environment = workflow.split("    steps:", 1)[0]
        self.assertIn("actions/cache/restore@", workflow)
        self.assertIn("actions/cache/save@", workflow)
        self.assertIn("CCACHE_MAXSIZE: 1Gi", workflow)
        self.assertIn("CCACHE_COMPILERCHECK: content", workflow)
        self.assertNotIn("${{ runner.", job_environment)
        self.assertIn(
            'printf \'CCACHE_DIR=%s\\n\' "${RUNNER_TEMP}/fex-arm64-ccache"',
            workflow,
        )
        self.assertIn("path: ${{ runner.temp }}/fex-arm64-ccache", workflow)
        self.assertIn(
            "if: ${{ inputs.profile == 'arm64-disk-cache-shapes-v1' }}",
            workflow,
        )
        self.assertNotIn("/build\n", workflow)

        profile = (
            REPO_ROOT
            / "Scripts"
            / "ResearchProfiles"
            / "arm64-disk-cache-shapes-v1"
            / "run.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("ccache --print-stats", profile)
        self.assertNotIn("ccache --print-log-stats", profile)


if __name__ == "__main__":
    unittest.main()
