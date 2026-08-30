#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("ResearchDevBuild.py")
SPEC = importlib.util.spec_from_file_location("research_dev_build", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
dev_build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev_build)


class ResearchDevBuildTest(unittest.TestCase):
    @staticmethod
    def doctor_runner(
        *, dirty: bool = False, submodules: str | None = None
    ):
        pinned = submodules or f" {'a' * 40} ThunkLibs/one (heads/main)\n"

        def run(command, **kwargs):
            git_environment = kwargs["env"]
            if git_environment["GIT_OPTIONAL_LOCKS"] != "0":
                raise AssertionError("doctor Git inspection may not refresh the index")
            if command[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, stdout=f"{'b' * 40}\n", stderr="")
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(
                    command, 0, stdout=" M file.cpp\n" if dirty else "", stderr=""
                )
            if command[-3:] == ["submodule", "status", "--recursive"]:
                return subprocess.CompletedProcess(command, 0, stdout=pinned, stderr="")
            raise AssertionError(command)

        return run

    def test_doctor_parser_and_ready_receipt_are_read_only_and_bounded(self):
        args = dev_build.parser().parse_args(["doctor"])
        self.assertEqual(args.action, "doctor")
        receipt = dev_build.doctor_receipt(
            Path("/source"),
            finder=lambda name: f"/tool/{name}",
            runner=self.doctor_runner(),
            machine="x86_64",
        )

        self.assertEqual(receipt["format"], "teamleaderleo-fex-experiment-doctor-v2")
        self.assertEqual(receipt["status"], "preflight_ready")
        self.assertEqual(receipt["source"]["head"], "b" * 40)
        self.assertEqual(receipt["submodules"]["repositories"], 1)
        self.assertEqual(
            receipt["capabilities"]["focusedX86HostBuildAndCTest"]["state"],
            "preflight_ready",
        )
        focused = receipt["capabilities"]["focusedX86HostBuildAndCTest"]
        self.assertTrue(focused["preflightReady"])
        self.assertEqual(focused["executionState"], "not_run")
        self.assertEqual(focused["evidenceState"], "not_established")
        self.assertNotIn("ready", focused)
        self.assertEqual(
            focused["nextCommands"],
            [
                "./Scripts/ResearchDevBuild.py --lane NAME build TARGET",
                "./Scripts/ResearchDevBuild.py --lane NAME check TARGET EXACT_CTEST",
                "./Scripts/ResearchDevBuild.py --lane editor editor",
            ],
        )
        self.assertEqual(
            receipt["capabilities"]["arm64ProductRuntime"]["state"],
            "escalate_to_checked_in_arm64_profile",
        )
        self.assertIn("no configure", receipt["mutation"])

    def test_doctor_reports_missing_tool_and_uninitialized_submodule(self):
        receipt = dev_build.doctor_receipt(
            Path("/source"),
            finder=lambda name: None if name == "nasm" else f"/tool/{name}",
            runner=self.doctor_runner(
                submodules=(
                    f"-{'c' * 40} ThunkLibs/missing\n"
                    f"+{'d' * 40} ThunkLibs/drifted (heads/other)\n"
                )
            ),
            machine="x86_64",
        )

        self.assertEqual(receipt["status"], "blocked")
        self.assertEqual(receipt["tools"]["nasm"]["state"], "missing")
        self.assertEqual(receipt["submodules"]["uninitialized"], ["ThunkLibs/missing"])
        self.assertEqual(receipt["submodules"]["drifted"], ["ThunkLibs/drifted"])
        self.assertIn("submodule update", receipt["submodules"]["remediation"])
        self.assertEqual(
            receipt["capabilities"]["focusedX86HostBuildAndCTest"]["blockers"],
            ["missing_tools", "submodules"],
        )
        self.assertFalse(
            receipt["capabilities"]["focusedX86HostBuildAndCTest"]["preflightReady"]
        )
        self.assertEqual(
            receipt["capabilities"]["focusedX86HostBuildAndCTest"]["nextCommands"],
            [],
        )

    def test_doctor_marks_dirty_source_as_feedback_only_without_blocking(self):
        receipt = dev_build.doctor_receipt(
            Path("/source"),
            finder=lambda name: f"/tool/{name}",
            runner=self.doctor_runner(dirty=True),
            machine="x86_64",
        )

        self.assertEqual(receipt["status"], "preflight_ready")
        self.assertEqual(
            receipt["capabilities"]["reusableExactHeadEvidence"]["state"],
            "feedback_only",
        )
        self.assertFalse(
            receipt["capabilities"]["reusableExactHeadEvidence"]["established"]
        )

    def test_doctor_refuses_to_call_arm_host_runtime_ready(self):
        receipt = dev_build.doctor_receipt(
            Path("/source"),
            finder=lambda name: f"/tool/{name}",
            runner=self.doctor_runner(),
            machine="aarch64",
        )

        self.assertEqual(receipt["status"], "blocked")
        self.assertEqual(
            receipt["capabilities"]["arm64ProductRuntime"]["state"],
            "candidate_requires_checked_in_profile",
        )
        arm_runtime = receipt["capabilities"]["arm64ProductRuntime"]
        self.assertFalse(arm_runtime["preflightReady"])
        self.assertEqual(arm_runtime["executionState"], "not_run")
        self.assertEqual(arm_runtime["evidenceState"], "not_established")
        self.assertNotIn("ready", arm_runtime)

    def test_configure_profile_is_host_debug_without_lto(self):
        with mock.patch.object(dev_build, "required_tool", return_value="/tool/cmake"):
            command = dev_build.configure_command(Path("/view/src"), Path("/view/build"))

        self.assertIn("-DENABLE_X86_HOST_DEBUG=True", command)
        self.assertIn("-DENABLE_LTO=False", command)
        self.assertIn("-DBUILD_THUNKS=True", command)
        self.assertIn("-DUSE_LINKER=lld", command)
        self.assertNotIn("-DBUILD_FEX_LINUX_TESTS=True", command)

    def test_environment_enforces_and_records_fex_time_macro_policy(self):
        with mock.patch.object(dev_build, "required_tool", side_effect=lambda name: f"/tool/{name}"):
            environment = dev_build.environment(Path("/cache"), Path("/lane"))

        self.assertEqual(environment["CCACHE_SLOPPINESS"], "time_macros")
        marker = dev_build.expected_profile(environment["CCACHE_NAMESPACE"])
        self.assertEqual(marker["ccacheSloppiness"], "time_macros")

    def test_linux_test_profile_adds_only_the_explicit_test_surface(self):
        profile = dev_build.CONFIGURE_PROFILES["linux-tests"]
        with mock.patch.object(dev_build, "required_tool", return_value="/tool/cmake"):
            command = dev_build.configure_command(
                Path("/view/src"), Path("/view/build"), list(profile["options"])
            )

        self.assertEqual(profile["id"], "x86-host-linux-tests-v1")
        self.assertIn("-DBUILD_FEX_LINUX_TESTS=True", command)
        self.assertIn("-DENABLE_X86_HOST_DEBUG=True", command)
        self.assertNotIn("-DBUILD_FEX_LINUX_TESTS=True", dev_build.CONFIGURE_OPTIONS)

    def test_editor_reconfigure_keeps_warm_build_tree(self):
        with mock.patch.object(dev_build, "required_tool", return_value="/tool/cmake"):
            command = dev_build.reconfigure_command(Path("/view/src"), Path("/view/build"))

        self.assertNotIn("--fresh", command)
        self.assertEqual(command[:6], [
            "/tool/cmake",
            "-S",
            "/view/src",
            "-B",
            "/view/build",
            "-G",
        ])
        self.assertIn("-DBUILD_TESTING=True", command)

    def test_editor_prerequisites_are_two_exact_generator_targets(self):
        with mock.patch.object(dev_build, "required_tool", return_value="/tool/cmake"):
            command = dev_build.editor_prerequisites_command(Path("/view/build"))

        self.assertEqual(command, [
            "/tool/cmake",
            "--build",
            "/view/build",
            "--target",
            "CONFIG_INC",
            "IR_INC",
            "--parallel",
            "2",
        ])

    def test_editor_prerequisites_require_all_declared_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            for path in dev_build.EDITOR_GENERATED_OUTPUTS:
                destination = build / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("generated\n", encoding="utf-8")

            dev_build.verify_editor_prerequisites(build)
            (build / dev_build.EDITOR_GENERATED_OUTPUTS[0]).unlink()
            with self.assertRaisesRegex(RuntimeError, "ConfigValues.inl"):
                dev_build.verify_editor_prerequisites(build)

    def test_build_requires_one_explicit_target(self):
        with mock.patch.object(dev_build, "required_tool", return_value="/tool/cmake"):
            command = dev_build.build_command(Path("/view/build"), "vulkan-host-64", 8)
            with self.assertRaises(ValueError):
                dev_build.build_command(Path("/view/build"), "--all", 8)

        self.assertEqual(command[-4:], ["--target", "vulkan-host-64", "--parallel", "8"])

    def test_plan_parser_and_command_are_exact_dry_run(self):
        args = dev_build.parser().parse_args(["plan", "vulkan-host-64"])
        self.assertEqual((args.action, args.target), ("plan", "vulkan-host-64"))
        with mock.patch.object(dev_build, "required_tool", return_value="/tool/ninja"):
            command = dev_build.ninja_plan_command(
                Path("/lane/build"), Path("/lane/build/.plan.ninja"), args.target
            )
            with self.assertRaises(ValueError):
                dev_build.ninja_plan_command(
                    Path("/lane/build"), Path("/lane/build/.plan.ninja"), "--all"
                )

        self.assertEqual(command[0], "/tool/ninja")
        self.assertIn("-n", command)
        self.assertEqual(command[-1], "vulkan-host-64")

    def test_discovery_parser_is_literal_and_bounded(self):
        args = dev_build.parser().parse_args(["discover", "Vulkan.*", "--limit", "4"])
        self.assertEqual((args.action, args.query, args.limit), ("discover", "Vulkan.*", 4))
        self.assertEqual(
            dev_build.validate_discovery_query("regex.*stays-literal"),
            "regex.*stays-literal",
        )
        self.assertEqual(dev_build.validate_discovery_limit(64), 64)
        for invalid in ("", " padded", "padded ", "two\nlines", "nul\0byte", "x" * 129):
            with self.assertRaises(ValueError):
                dev_build.validate_discovery_query(invalid)
        with self.assertRaises(ValueError):
            dev_build.validate_discovery_limit(65)

    def test_configured_target_registry_extracts_semantic_headings(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "build.ninja"
            manifest.write_text(
                "# Object build statements for EXECUTABLE target Tool\n"
                "# Link build statements for EXECUTABLE target Tool\n"
                "# Utility command for generated-assets\n"
                "# Utility command for generated-assets\n"
                "build object.o: CXX source.cpp\n",
                encoding="utf-8",
            )
            registry = dev_build.configured_target_registry(manifest)

        self.assertEqual(
            registry["targets"],
            [
                {"name": "generated-assets", "type": "UTILITY"},
                {"name": "Tool", "type": "EXECUTABLE"},
            ],
        )
        self.assertRegex(registry["digest"], r"^[0-9a-f]{64}$")

    def test_configured_target_registry_rejects_empty_and_ambiguous(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "build.ninja"
            manifest.write_text("build object.o: CXX source.cpp\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "registry is empty"):
                dev_build.configured_target_registry(manifest)
            manifest.write_text(
                "# Utility command for same\n"
                "# Object build statements for EXECUTABLE target same\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                dev_build.configured_target_registry(manifest)

    def test_configured_test_registry_and_literal_matches(self):
        payload = json.dumps(
            {
                "tests": [
                    {"name": "Vulkan.Inventory", "command": ["/usr/bin/python3", "test.py"]},
                    {"name": "vulkan_NOT_BUILT", "command": None},
                    {"name": "Other", "command": ["/bin/true"]},
                ]
            }
        )
        registry = dev_build.configured_test_registry(payload)
        matches = dev_build.literal_matches(registry["tests"], "VULKAN", 1)

        self.assertEqual(matches["totalMatches"], 2)
        self.assertEqual(matches["returned"], 1)
        self.assertTrue(matches["truncated"])
        self.assertEqual(matches["results"][0]["commandHead"], "python3")
        self.assertRegex(registry["digest"], r"^[0-9a-f]{64}$")

    def test_configured_test_registry_rejects_malformed_duplicate_and_oversized(self):
        for payload in (
            "not-json",
            json.dumps({"wrong": []}),
            json.dumps(
                {
                    "tests": [
                        {"name": "same", "command": None},
                        {"name": "same", "command": None},
                    ]
                }
            ),
            json.dumps({"tests": [{"name": "bad", "command": []}]}),
            json.dumps({"tests": [{"name": "bad", "command": [""]}]}),
        ):
            with self.assertRaises(RuntimeError):
                dev_build.configured_test_registry(payload)
        with mock.patch.object(dev_build, "DISCOVERY_REGISTRY_LIMIT", 4):
            with self.assertRaisesRegex(RuntimeError, "bounded output"):
                dev_build.configured_test_registry('{"tests": []}')

    def test_shadow_plan_manifest_removes_only_verify_edge_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            manifest = build / "build.ninja"
            manifest.write_text(
                "rule VERIFY_GLOBS\n  command = cmake -P VerifyGlobs.cmake\n"
                "rule RERUN_CMAKE\n  command = cmake --regenerate-during-build\n"
                "build verify.force: phony\n"
                "build verify.stamp: VERIFY_GLOBS | verify.force\n  pool = console\n\n"
                "build build.ninja: RERUN_CMAKE verify.stamp | CMakeLists.txt\n"
                "  pool = console\n\n"
                "build object.o: CXX source.cpp\n",
                encoding="utf-8",
            )
            with dev_build.shadow_plan_manifest(build) as shadow:
                payload = shadow.read_text(encoding="utf-8")
                shadow_path = shadow
                self.assertNotIn(": VERIFY_GLOBS ", payload)
                self.assertIn("rule FEX_PLAN_RERUN_CMAKE", payload)
                self.assertIn(": FEX_PLAN_RERUN_CMAKE ", payload)
                self.assertIn(f"build {shadow}:", payload)
                self.assertNotIn("build build.ninja: RERUN_CMAKE", payload)
                self.assertIn("build object.o: CXX source.cpp", payload)
            self.assertFalse(shadow_path.exists())
            self.assertIn(": VERIFY_GLOBS ", manifest.read_text(encoding="utf-8"))

    def test_shadow_plan_manifest_rejects_unknown_regeneration_shape(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            (build / "build.ninja").write_text("build object.o: CXX source.cpp\n")
            with self.assertRaisesRegex(RuntimeError, "unsupported Ninja regeneration graph"):
                with dev_build.shadow_plan_manifest(build):
                    self.fail("unsupported graph must not yield")

    def test_discovery_graph_preflight_accepts_current_and_refuses_stale(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            (build / "CMakeFiles").mkdir()
            (build / "CMakeFiles" / "VerifyGlobs.cmake").write_text("", encoding="utf-8")
            (build / "build.ninja").write_text(
                "rule VERIFY_GLOBS\n  command = cmake -P VerifyGlobs.cmake\n"
                "rule RERUN_CMAKE\n  command = cmake --regenerate-during-build\n"
                "build verify.force: phony\n"
                "build verify.stamp: VERIFY_GLOBS | verify.force\n\n"
                "build build.ninja: RERUN_CMAKE verify.stamp | CMakeLists.txt\n\n",
                encoding="utf-8",
            )

            def runner_for(ninja_stdout):
                def runner(command, **kwargs):
                    if command[0] == "/tool/cmake":
                        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                    return subprocess.CompletedProcess(command, 0, stdout=ninja_stdout, stderr="")
                return runner

            with mock.patch.object(dev_build, "required_tool", side_effect=lambda name: f"/tool/{name}"):
                with mock.patch.object(
                    dev_build.subprocess,
                    "run",
                    side_effect=runner_for("ninja: no work to do.\n"),
                ):
                    dev_build.require_current_discovery_graph(build, {})
                with mock.patch.object(
                    dev_build.subprocess,
                    "run",
                    side_effect=runner_for(
                        f"[1/1] {dev_build.PLAN_REGEN_DESCRIPTION}\n"
                    ),
                ):
                    with self.assertRaisesRegex(RuntimeError, "regenerate the lane"):
                        dev_build.require_current_discovery_graph(build, {})

    def test_parse_ninja_plan_counts_and_flags_cmake_regeneration(self):
        plan = dev_build.parse_ninja_plan(
            "ninja: Entering directory `/lane/build'\n"
            "[1/2] Building CXX object object.o\n"
            "[2/2] Linking CXX executable tool\n",
            "ninja explain: source.cpp is dirty\n",
        )
        self.assertEqual(plan["plannedSteps"], 2)
        self.assertEqual(plan["reasons"], 1)
        self.assertFalse(plan["requiresCMakeRegeneration"])

        stale = dev_build.parse_ninja_plan(
            f"[1/1] {dev_build.PLAN_REGEN_DESCRIPTION}\n", ""
        )
        self.assertTrue(stale["requiresCMakeRegeneration"])

    def test_plan_lane_requires_current_source_graph_and_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            other = root / "other"
            lane = root / "lane"
            build = lane / "build"
            source.mkdir()
            other.mkdir()
            build.mkdir(parents=True)
            os.symlink(source, lane / "src", target_is_directory=True)
            (build / "build.ninja").write_text("", encoding="utf-8")
            expected = dev_build.expected_profile("namespace")
            dev_build.write_receipt(lane / "profile.json", expected)

            dev_build.require_plan_lane(
                source, lane / "src", build, lane / "profile.json", expected
            )
            with self.assertRaisesRegex(RuntimeError, "refuses to switch"):
                dev_build.require_plan_lane(
                    other, lane / "src", build, lane / "profile.json", expected
                )

    def test_plan_action_emits_receipt_without_replacing_build_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            cache = root / "cache"
            lane = cache / "views" / "preview"
            build = lane / "build"
            source.mkdir()
            build.mkdir(parents=True)
            os.symlink(source, lane / "src", target_is_directory=True)
            (build / "CMakeFiles").mkdir()
            (build / "CMakeFiles" / "VerifyGlobs.cmake").write_text("", encoding="utf-8")
            (build / "build.ninja").write_text(
                "rule VERIFY_GLOBS\n  command = cmake -P VerifyGlobs.cmake\n"
                "rule RERUN_CMAKE\n  command = cmake --regenerate-during-build\n"
                "build verify.force: phony\n"
                "build verify.stamp: VERIFY_GLOBS | verify.force\n\n"
                "build build.ninja: RERUN_CMAKE verify.stamp | CMakeLists.txt\n\n"
                "build tool: phony object.o\n",
                encoding="utf-8",
            )
            for name, payload in (
                (".ninja_log", "log\n"),
                (".ninja_deps", "deps\n"),
                ("last-receipt.json", '{"prior": true}\n'),
            ):
                (build / name if name.startswith(".ninja") else lane / name).write_text(
                    payload, encoding="utf-8"
                )
            expected = dev_build.expected_profile("namespace")
            dev_build.write_receipt(lane / "profile.json", expected)
            before = (lane / "last-receipt.json").read_bytes()
            output = __import__("io").StringIO()

            def runner(command, **kwargs):
                if command[-2:] == ["-P", str(build / "CMakeFiles" / "VerifyGlobs.cmake")]:
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                self.assertIn("-n", command)
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="[1/1] Linking CXX executable tool\n",
                    stderr="ninja explain: object.o is dirty\n",
                )

            with mock.patch.object(dev_build, "require_pinned_submodules"):
                with mock.patch.object(
                    dev_build, "required_tool", side_effect=lambda name: f"/tool/{name}"
                ):
                    with mock.patch.object(
                        dev_build,
                        "environment",
                        return_value={
                            "CCACHE_NAMESPACE": "namespace",
                            "CCACHE_SLOPPINESS": "time_macros",
                        },
                    ):
                        with mock.patch.object(
                            dev_build,
                            "source_identity",
                            return_value={"head": "a" * 40, "dirty": True},
                        ):
                            with mock.patch.object(dev_build.subprocess, "run", side_effect=runner):
                                with mock.patch("sys.stdout", output):
                                    result = dev_build.main(
                                        [
                                            "--source",
                                            str(source),
                                            "--cache-root",
                                            str(cache),
                                            "--lane",
                                            "preview",
                                            "plan",
                                            "tool",
                                        ]
                                    )

            receipt = json.loads(output.getvalue())
            self.assertEqual(result, 0)
            self.assertEqual(receipt["plan"]["plannedSteps"], 1)
            self.assertTrue(receipt["protectedStateUnchanged"])
            self.assertFalse(receipt["targetCommandsExecuted"])
            self.assertEqual((lane / "last-receipt.json").read_bytes(), before)
            self.assertEqual(list(build.glob(".fex-plan-*.ninja")), [])

    def test_submodule_action_uses_bounded_parallel_shallow_update(self):
        with mock.patch.object(dev_build, "required_tool", return_value="/tool/git"):
            command = dev_build.submodule_update_command(Path("/worktree"), 16)

        self.assertEqual(
            command,
            [
                "/tool/git",
                "-C",
                "/worktree",
                "submodule",
                "update",
                "--init",
                "--recursive",
                "--depth",
                "1",
                "--jobs",
                "16",
            ],
        )
        args = dev_build.parser().parse_args(["submodules", "--jobs", "4"])
        self.assertEqual((args.action, args.jobs), ("submodules", 4))
        cached = dev_build.parser().parse_args(["submodules", "--pack-cache"])
        self.assertTrue(cached.pack_cache)
        seeded = dev_build.parser().parse_args(["submodules", "--origin-cache"])
        self.assertTrue(seeded.origin_cache)
        inventory = dev_build.parser().parse_args(["submodule-cache"])
        self.assertEqual(inventory.action, "submodule-cache")
        origin_inventory = dev_build.parser().parse_args(["submodule-origin-cache"])
        self.assertEqual(origin_inventory.action, "submodule-origin-cache")

    def test_linux_test_commands_name_one_guest_build(self):
        with mock.patch.object(dev_build, "required_tool", side_effect=lambda name: f"/tool/{name}"):
            configure = dev_build.configure_linux_test_command(
                Path("/view/src"), Path("/view/build/guest"), 64
            )

        self.assertIn("-DBITNESS=64", configure)
        self.assertIn("toolchain_x86_64.cmake", " ".join(configure))
        self.assertEqual(dev_build.validate_linux_test("smc-2"), "smc-2")
        for invalid in ("", "../smc-2", "smc-2|other", "--all"):
            with self.assertRaises(ValueError):
                dev_build.validate_linux_test(invalid)

        fex = dev_build.build_command(Path("/view/build"), "FEX", 8)
        server = dev_build.build_command(Path("/view/build"), "FEXServer", 8)
        self.assertEqual(fex[-4:-2], ["--target", "FEX"])
        self.assertEqual(server[-4:-2], ["--target", "FEXServer"])

    def test_check_parser_and_ctest_name_are_exact(self):
        args = dev_build.parser().parse_args(
            ["check", "thunkgentest", "VulkanCustomRouteInventory.ThunkGen"]
        )
        self.assertEqual(
            (args.action, args.target, args.test),
            ("check", "thunkgentest", "VulkanCustomRouteInventory.ThunkGen"),
        )
        self.assertEqual(
            dev_build.validate_ctest_name("StructRepacking.ThunkGen"),
            "StructRepacking.ThunkGen",
        )
        self.assertEqual(
            dev_build.validate_ctest_name("regex.*characters.[stay]-literal"),
            "regex.*characters.[stay]-literal",
        )
        for invalid in ("", " leading", "trailing ", "two\nlines", "nul\0byte"):
            with self.assertRaises(ValueError):
                dev_build.validate_ctest_name(invalid)

    def test_ctest_command_uses_exact_name_file_and_no_tests_error(self):
        with mock.patch.object(dev_build, "required_tool", return_value="/tool/ctest"):
            command = dev_build.ctest_command(
                Path("/lane/build"), Path("/lane/tests.txt")
            )

        self.assertEqual(command[0], "/tool/ctest")
        self.assertIn("--tests-from-file", command)
        self.assertIn("/lane/tests.txt", command)
        self.assertIn("--no-tests=error", command)
        self.assertNotIn("-R", command)

    def test_generated_ctest_name_supports_current_cmake_forms(self):
        self.assertEqual(
            dev_build.generated_ctest_name("add_test(Bare.Name /bin/true)"),
            "Bare.Name",
        )
        self.assertEqual(
            dev_build.generated_ctest_name(
                "add_test( [==[name with spaces.*]==] /bin/true)"
            ),
            "name with spaces.*",
        )
        self.assertIsNone(dev_build.generated_ctest_name("set(value add_test(fake))"))
        for invalid in ('add_test("escaped\\nname" /bin/true)', "add_test(NAME x)"):
            with self.assertRaises(RuntimeError):
                dev_build.generated_ctest_name(invalid)

    def test_generated_ctest_registry_counts_literal_names_and_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            nested = build / "nested"
            nested.mkdir()
            (build / "CTestTestfile.cmake").write_text(
                "add_test(Unique.Test /bin/true)\n",
                encoding="utf-8",
            )
            (nested / "generated_tests.cmake").write_text(
                "add_test( [=[Duplicate.Test]=] /bin/true)\n"
                "add_test(Duplicate.Test /bin/true)\n",
                encoding="utf-8",
            )
            registry = dev_build.generated_ctest_registry(build)

        self.assertEqual(registry["files"], 2)
        self.assertEqual(registry["definitions"], 3)
        self.assertEqual(registry["names"].count("Unique.Test"), 1)
        self.assertEqual(registry["names"].count("Duplicate.Test"), 2)
        self.assertRegex(registry["digest"], r"^[0-9a-f]{64}$")

    def test_lane_names_cannot_escape_cache_root(self):
        self.assertEqual(dev_build.validate_lane("callback-fix.2"), "callback-fix.2")
        for invalid in ("../other", "/tmp/other", "two lanes", ""):
            with self.assertRaises(ValueError):
                dev_build.validate_lane(invalid)

    def test_missing_or_mismatched_submodules_fail_before_cmake(self):
        status = """-aaaaaaaa External/missing
+bbbbbbbb External/wrong (heads/main)
 cccccccc External/ready (heads/main)
"""
        completed = subprocess.CompletedProcess([], 0, stdout=status)
        with mock.patch.object(dev_build, "required_tool", return_value="/tool/git"):
            with mock.patch.object(dev_build.subprocess, "run", return_value=completed):
                with self.assertRaisesRegex(
                    RuntimeError,
                    r"External/missing, External/wrong.*submodule update --init --recursive --depth 1 --jobs",
                ):
                    dev_build.require_pinned_submodules(Path("/worktree"))

    def test_pinned_submodules_pass_preflight(self):
        completed = subprocess.CompletedProcess(
            [], 0, stdout=" aaaaaaaa External/ready (heads/main)\n"
        )
        with mock.patch.object(dev_build, "required_tool", return_value="/tool/git"):
            with mock.patch.object(dev_build.subprocess, "run", return_value=completed):
                dev_build.require_pinned_submodules(Path("/worktree"))

    def test_pinned_submodule_identity_is_content_addressed(self):
        status = """ 2222222222222222222222222222222222222222 External/zeta (heads/main)
 1111111111111111111111111111111111111111 External/alpha (v1.0)
"""
        completed = subprocess.CompletedProcess([], 0, stdout=status)
        expected = __import__("hashlib").sha256(
            (
                "1111111111111111111111111111111111111111 External/alpha\n"
                "2222222222222222222222222222222222222222 External/zeta\n"
            ).encode("utf-8")
        ).hexdigest()
        with mock.patch.object(dev_build, "required_tool", return_value="/tool/git"):
            with mock.patch.object(dev_build.subprocess, "run", return_value=completed):
                count, digest = dev_build.pinned_submodule_identity(Path("/worktree"))

        self.assertEqual(count, 2)
        self.assertEqual(digest, expected)

    def test_submodule_action_verifies_and_emits_receipt_without_build_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            output = __import__("io").StringIO()
            progress = __import__("io").StringIO()
            completed = subprocess.CompletedProcess(["git", "submodule", "update"], 0)
            with mock.patch.object(
                dev_build,
                "submodule_update_command",
                return_value=["/tool/git", "submodule", "update"],
            ):
                with mock.patch.object(dev_build.subprocess, "run", return_value=completed) as run:
                    with mock.patch.object(dev_build, "require_pinned_submodules") as require:
                        with mock.patch.object(
                            dev_build,
                            "pinned_submodule_identity",
                            return_value=(18, "a" * 64),
                        ):
                            with mock.patch.object(
                                dev_build,
                                "source_identity",
                                return_value={"head": "b" * 40, "dirty": False},
                            ):
                                with mock.patch.object(
                                    dev_build.time, "monotonic", side_effect=(10.0, 12.5)
                                ):
                                    with mock.patch("sys.stdout", output):
                                        with mock.patch("sys.stderr", progress):
                                            result = dev_build.main(
                                                [
                                                    "--source",
                                                    str(source),
                                                    "submodules",
                                                    "--jobs",
                                                    "4",
                                                ]
                                            )

        receipt = __import__("json").loads(output.getvalue())
        self.assertEqual(result, 0)
        run.assert_called_once_with(
            ["/tool/git", "submodule", "update"], check=True, stdout=progress
        )
        require.assert_called_once_with(source.resolve())
        self.assertEqual(receipt["repositories"], 18)
        self.assertEqual(receipt["pinnedDigest"], "a" * 64)
        self.assertEqual(receipt["elapsedSeconds"], 2.5)
        self.assertEqual(receipt["jobs"], 4)

    def test_submodule_action_attaches_pack_cache_receipt_and_rechecks_pins(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            output = __import__("io").StringIO()
            completed = subprocess.CompletedProcess(["git", "submodule", "update"], 0)
            with mock.patch.object(
                dev_build,
                "submodule_update_command",
                return_value=["/tool/git", "submodule", "update"],
            ):
                with mock.patch.object(dev_build.subprocess, "run", return_value=completed):
                    with mock.patch.object(dev_build, "require_pinned_submodules") as require:
                        with mock.patch.object(
                            dev_build,
                            "pinned_submodule_identity",
                            return_value=(18, "a" * 64),
                        ):
                            with mock.patch.object(
                                dev_build.submodule_pack_cache,
                                "compact",
                                return_value={"format": "cache", "linkedEntries": 72},
                            ) as compact:
                                with mock.patch.object(
                                    dev_build,
                                    "source_identity",
                                    return_value={"head": "b" * 40, "dirty": False},
                                ):
                                    with mock.patch("sys.stdout", output):
                                        result = dev_build.main(
                                            [
                                                "--source",
                                                str(source),
                                                "--cache-root",
                                                str(source / "cache"),
                                                "submodules",
                                                "--pack-cache",
                                            ]
                                        )

        receipt = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(receipt["packCache"]["linkedEntries"], 72)
        self.assertEqual(require.call_count, 2)
        compact.assert_called_once_with(source.resolve(), (source / "cache").resolve(), "a" * 64, 18)

    def test_submodule_action_composes_origin_and_pack_cache_receipts(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            output = __import__("io").StringIO()
            with mock.patch.object(
                dev_build.submodule_origin_cache,
                "update",
                return_value={"format": "origin-cache", "state": "warm_hit"},
            ) as update:
                with mock.patch.object(dev_build, "require_pinned_submodules") as require:
                    with mock.patch.object(
                        dev_build,
                        "pinned_submodule_identity",
                        return_value=(18, "a" * 64),
                    ):
                        with mock.patch.object(
                            dev_build.submodule_pack_cache,
                            "compact",
                            return_value={"format": "pack-cache", "linkedEntries": 54},
                        ) as compact:
                            with mock.patch.object(
                                dev_build,
                                "source_identity",
                                return_value={"head": "b" * 40, "dirty": False},
                            ):
                                with mock.patch("sys.stdout", output):
                                    result = dev_build.main(
                                        [
                                            "--source",
                                            str(source),
                                            "--cache-root",
                                            str(source / "cache"),
                                            "submodules",
                                            "--origin-cache",
                                            "--pack-cache",
                                            "--jobs",
                                            "4",
                                        ]
                                    )

        receipt = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(receipt["originCache"]["state"], "warm_hit")
        self.assertEqual(receipt["packCache"]["linkedEntries"], 54)
        self.assertEqual(require.call_count, 2)
        update.assert_called_once_with(
            source.resolve(),
            (source / "cache").resolve(),
            4,
            progress=mock.ANY,
        )
        compact.assert_called_once_with(
            source.resolve(), (source / "cache").resolve(), "a" * 64, 18
        )

    def test_submodule_action_defers_pack_cache_for_cold_origin_population(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            output = __import__("io").StringIO()
            with mock.patch.object(
                dev_build.submodule_origin_cache,
                "update",
                return_value={"format": "origin-cache", "state": "cold_populated"},
            ):
                with mock.patch.object(dev_build, "require_pinned_submodules") as require:
                    with mock.patch.object(
                        dev_build,
                        "pinned_submodule_identity",
                        return_value=(18, "a" * 64),
                    ):
                        with mock.patch.object(
                            dev_build.submodule_pack_cache, "compact"
                        ) as compact:
                            with mock.patch.object(
                                dev_build,
                                "source_identity",
                                return_value={"head": "b" * 40, "dirty": False},
                            ):
                                with mock.patch("sys.stdout", output):
                                    result = dev_build.main(
                                        [
                                            "--source",
                                            str(source),
                                            "--cache-root",
                                            str(source / "cache"),
                                            "submodules",
                                            "--origin-cache",
                                            "--pack-cache",
                                        ]
                                    )

        receipt = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(receipt["originCache"]["state"], "cold_populated")
        self.assertEqual(
            receipt["packCache"]["state"], "deferred_until_warm_origin"
        )
        self.assertEqual(require.call_count, 1)
        compact.assert_not_called()

    def test_lane_inventory_classifies_live_dead_active_and_unsafe_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            views = cache / "views"
            locks = cache / "locks"
            live_source = root / "live-source"
            outside = root / "outside"
            live_source.mkdir()
            outside.mkdir()
            (outside / "large").write_bytes(b"x" * (1024 * 1024))
            locks.mkdir(parents=True)

            live = views / "live"
            (live / "build").mkdir(parents=True)
            os.symlink(live_source, live / "src", target_is_directory=True)
            (live / "build" / "build.ninja").write_text("", encoding="utf-8")
            os.symlink(outside, live / "outside", target_is_directory=True)
            dev_build.write_receipt(
                live / "last-receipt.json",
                {"format": "receipt", "head": "a" * 40, "dirty": False, "exitCode": 0},
            )
            dev_build.write_receipt(live / "profile.json", {"format": "profile"})
            live_lock = (locks / "live.lock").open("a+", encoding="utf-8")
            fcntl = __import__("fcntl")
            fcntl.flock(live_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

            dead = views / "dead"
            dead.mkdir(parents=True)
            os.symlink(root / "removed", dead / "src", target_is_directory=True)
            dev_build.write_receipt(
                dead / "last-receipt.json",
                {"format": "receipt", "head": "b" * 40, "dirty": False, "exitCode": 0},
            )
            dev_build.write_receipt(dead / "profile.json", {"format": "profile"})

            unsafe = views / "unsafe"
            unsafe.mkdir(parents=True)
            (unsafe / "src").write_text("not a symlink", encoding="utf-8")
            (unsafe / "last-receipt.json").write_text("not json", encoding="utf-8")

            inventory = dev_build.lane_inventory(cache)
            records = {record["lane"]: record for record in inventory["lanes"]}
            fcntl.flock(live_lock, fcntl.LOCK_UN)
            live_lock.close()

        self.assertEqual(inventory["totals"]["lanes"], 3)
        self.assertEqual(inventory["totals"]["bySourceState"], {
            "live": 1,
            "dead": 1,
            "missing": 0,
            "unsafe": 1,
        })
        self.assertEqual(records["live"]["sourceState"], "live")
        self.assertEqual(records["live"]["lockState"], "active")
        self.assertEqual(records["live"]["buildState"], "configured")
        self.assertEqual(records["live"]["receiptState"], "valid")
        self.assertLess(records["live"]["allocatedBytes"], 1024 * 1024)
        self.assertEqual(records["dead"]["sourceState"], "dead")
        self.assertEqual(records["dead"]["lockState"], "missing")
        self.assertTrue(records["dead"]["reviewCandidate"])
        self.assertEqual(records["unsafe"]["sourceState"], "unsafe")
        self.assertFalse(records["unsafe"]["reviewCandidate"])

    def test_lane_inventory_rejects_symlinked_views_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            outside = root / "outside"
            outside.mkdir()
            cache.mkdir()
            os.symlink(outside, cache / "views", target_is_directory=True)

            with self.assertRaisesRegex(RuntimeError, "views root is not a directory"):
                dev_build.lane_inventory(cache)

    def test_lanes_action_is_read_only_and_does_not_require_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "missing-cache"
            output = __import__("io").StringIO()
            with mock.patch("sys.stdout", output):
                result = dev_build.main(
                    [
                        "--source",
                        str(Path(temporary) / "missing-source"),
                        "--cache-root",
                        str(cache),
                        "lanes",
                    ]
                )

        inventory = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(inventory["totals"]["lanes"], 0)
        self.assertFalse(cache.exists())

    def test_profile_marker_must_match_exactly(self):
        expected = dev_build.expected_profile("cache-namespace")
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "profile.json"
            dev_build.write_receipt(marker, expected)
            self.assertTrue(dev_build.profile_matches(marker, expected))

            changed = dict(expected)
            changed["profile"] = "some-other-profile"
            self.assertFalse(dev_build.profile_matches(marker, changed))

            linux_profile = dev_build.CONFIGURE_PROFILES["linux-tests"]
            changed_options = dev_build.expected_profile(
                "cache-namespace",
                str(linux_profile["id"]),
                list(linux_profile["options"]),
            )
            self.assertFalse(dev_build.profile_matches(marker, changed_options))

            marker.write_text("not json", encoding="utf-8")
            self.assertFalse(dev_build.profile_matches(marker, expected))

    def test_matching_warm_worktree_switch_uses_incremental_configure(self):
        self.assertEqual(
            dev_build.configuration_mode(
                "build",
                switched=True,
                build_configured=True,
                profile_compatible=True,
            ),
            "incremental",
        )

    def test_configuration_mode_keeps_fail_closed_fresh_cases(self):
        self.assertEqual(
            dev_build.configuration_mode(
                "configure",
                switched=False,
                build_configured=True,
                profile_compatible=True,
            ),
            "fresh",
        )
        self.assertEqual(
            dev_build.configuration_mode(
                "build",
                switched=True,
                build_configured=False,
                profile_compatible=True,
            ),
            "fresh",
        )
        self.assertEqual(
            dev_build.configuration_mode(
                "build",
                switched=True,
                build_configured=True,
                profile_compatible=False,
            ),
            "fresh",
        )
        self.assertEqual(
            dev_build.configuration_mode(
                "build",
                switched=False,
                build_configured=True,
                profile_compatible=True,
            ),
            "reuse",
        )

    def test_editor_database_translates_stable_source_view(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_view = root / "lane" / "src"
            source = root / "worktree"
            build = root / "lane" / "build"
            destination = source / "compile_commands.json"
            build.mkdir(parents=True)
            source.mkdir()
            database = [
                {
                    "directory": str(build),
                    "command": f"clang++ -I{source_view}/include -c {source_view}/Source/a.cpp",
                    "file": str(source_view / "Source/a.cpp"),
                }
            ]
            (build / "compile_commands.json").write_text(
                __import__("json").dumps(database), encoding="utf-8"
            )

            count = dev_build.write_editor_compile_commands(
                source_view, source, build, destination
            )
            translated = __import__("json").loads(destination.read_text(encoding="utf-8"))

            self.assertEqual(count, 1)
            self.assertEqual(translated[0]["file"], str(source / "Source/a.cpp"))
            self.assertIn(f"-I{source}/include", translated[0]["command"])
            self.assertEqual(translated[0]["directory"], str(build))

    def test_worktree_switch_cleans_before_atomic_repoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "old"
            new = root / "new"
            old.mkdir()
            new.mkdir()
            lane = root / "lane"
            build = lane / "build"
            build.mkdir(parents=True)
            (build / "build.ninja").write_text("", encoding="utf-8")
            source_view = lane / "src"
            os.symlink(old, source_view, target_is_directory=True)
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs, source_view.resolve()))
                return subprocess.CompletedProcess(command, 0)

            with mock.patch.object(
                dev_build, "required_tool", side_effect=lambda name: f"/tool/{name}"
            ):
                switched = dev_build.prepare_source_view(
                    new.resolve(), source_view, build, {}, runner=runner
                )

            self.assertTrue(switched)
            self.assertEqual(calls[0][2], old.resolve())
            self.assertEqual(calls[0][0][0], "/tool/cmake")
            self.assertEqual(calls[0][0][-1], "clean")
            self.assertEqual(source_view.resolve(), new.resolve())

    def test_dead_worktree_switch_cleans_retained_graph_without_cmake(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "removed"
            new = root / "new"
            new.mkdir()
            lane = root / "lane"
            build = lane / "build"
            build.mkdir(parents=True)
            (build / "build.ninja").write_text("", encoding="utf-8")
            source_view = lane / "src"
            os.symlink(old, source_view, target_is_directory=True)
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs, source_view.resolve()))
                return subprocess.CompletedProcess(command, 0)

            with mock.patch.object(
                dev_build, "required_tool", side_effect=lambda name: f"/tool/{name}"
            ):
                switched = dev_build.prepare_source_view(
                    new.resolve(), source_view, build, {}, runner=runner
                )

            self.assertTrue(switched)
            self.assertEqual(
                calls[0][0],
                ["/tool/ninja", "-C", str(build), "-t", "clean"],
            )
            self.assertEqual(calls[0][2], old.resolve())
            self.assertEqual(source_view.resolve(), new.resolve())

    def test_dead_worktree_clean_failure_does_not_repoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "removed"
            new = root / "new"
            new.mkdir()
            lane = root / "lane"
            build = lane / "build"
            build.mkdir(parents=True)
            (build / "build.ninja").write_text("", encoding="utf-8")
            source_view = lane / "src"
            os.symlink(old, source_view, target_is_directory=True)

            with mock.patch.object(dev_build, "required_tool", return_value="/tool/ninja"):
                with self.assertRaises(subprocess.CalledProcessError):
                    dev_build.prepare_source_view(
                        new.resolve(),
                        source_view,
                        build,
                        {},
                        runner=mock.Mock(
                            side_effect=subprocess.CalledProcessError(1, ["ninja"])
                        ),
                    )

            self.assertEqual(source_view.resolve(), old.resolve())

    def test_dead_worktree_switch_refuses_symlinked_retained_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "removed"
            new = root / "new"
            new.mkdir()
            lane = root / "lane"
            build = lane / "build"
            build.mkdir(parents=True)
            external = root / "external.ninja"
            external.write_text("", encoding="utf-8")
            (build / "build.ninja").symlink_to(external)
            source_view = lane / "src"
            os.symlink(old, source_view, target_is_directory=True)

            with self.assertRaisesRegex(RuntimeError, "unsafe retained Ninja manifest"):
                dev_build.prepare_source_view(
                    new.resolve(), source_view, build, {}, runner=mock.Mock()
                )

            self.assertEqual(source_view.resolve(), old.resolve())

    @unittest.skipUnless(shutil.which("ninja"), "Ninja is required for the real clean fixture")
    def test_dead_worktree_real_ninja_clean_executes_no_graph_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "removed"
            new = root / "new"
            new.mkdir()
            lane = root / "lane"
            build = lane / "build"
            build.mkdir(parents=True)
            (build / "build.ninja").write_text(
                "rule forbidden\n"
                "  command = false\n"
                "build output.txt: forbidden\n"
                "default output.txt\n",
                encoding="utf-8",
            )
            output = build / "output.txt"
            output.write_text("retained output\n", encoding="utf-8")
            source_view = lane / "src"
            os.symlink(old, source_view, target_is_directory=True)

            switched = dev_build.prepare_source_view(
                new.resolve(), source_view, build, os.environ.copy()
            )

            self.assertTrue(switched)
            self.assertFalse(output.exists())
            self.assertTrue((build / "build.ninja").is_file())
            self.assertEqual(source_view.resolve(), new.resolve())

    def test_worktree_switch_cleans_focused_guest_tree_before_outer_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "old"
            new = root / "new"
            old.mkdir()
            new.mkdir()
            lane = root / "lane"
            build = lane / "build"
            guest_build = dev_build.focused_linux_test_build(build, 64)
            guest_build.mkdir(parents=True)
            (build / "build.ninja").write_text("", encoding="utf-8")
            (guest_build / "build.ninja").write_text("", encoding="utf-8")
            source_view = lane / "src"
            os.symlink(old, source_view, target_is_directory=True)
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0)

            with mock.patch.object(
                dev_build, "required_tool", side_effect=lambda name: f"/tool/{name}"
            ):
                dev_build.prepare_source_view(
                    new.resolve(),
                    source_view,
                    build,
                    {},
                    (guest_build,),
                    runner=runner,
                )

            self.assertEqual(calls[0][2], str(guest_build))
            self.assertEqual(calls[1][2], str(build))


if __name__ == "__main__":
    unittest.main()
