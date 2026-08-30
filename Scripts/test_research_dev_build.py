#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
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
                "./Scripts/ResearchDevBuild.py --lane editor compile SOURCE.cpp",
                "./Scripts/ResearchDevBuild.py --lane NAME build TARGET",
                "./Scripts/ResearchDevBuild.py --lane NAME check TARGET EXACT_CTEST",
                "./Scripts/ResearchDevBuild.py --lane NAME check-set TARGET",
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

    def test_editor_database_survives_failed_graph_regeneration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source_view = root / "source-view"
            build = root / "build"
            destination = source / "compile_commands.json"
            source.mkdir()
            build.mkdir()
            destination.write_text("prior database\n", encoding="utf-8")

            def fail(command, **kwargs):
                raise subprocess.CalledProcessError(1, command)

            with mock.patch.object(dev_build, "required_tool", return_value="/tool/cmake"):
                with self.assertRaises(subprocess.CalledProcessError):
                    dev_build.prepare_editor_database(
                        source_view, source, build, destination, {}, runner=fail
                    )

            self.assertEqual(destination.read_text(encoding="utf-8"), "prior database\n")

    @unittest.skipUnless(
        shutil.which("cmake") and shutil.which("ninja"),
        "CMake and Ninja are required for the regeneration fixture",
    )
    def test_editor_prerequisite_build_owns_cmake_regeneration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            build = root / "build"
            source.mkdir()
            template = source / "value.in"
            ordinary_source = source / "known.cpp"
            template.write_text("@VALUE@\n", encoding="utf-8")
            ordinary_source.write_text("int known;\n", encoding="utf-8")

            def project(value: str) -> str:
                return (
                    "cmake_minimum_required(VERSION 3.20)\n"
                    "project(EditorGraphFixture NONE)\n"
                    'file(APPEND "${CMAKE_BINARY_DIR}/configure-count.txt" "x")\n'
                    f'set(VALUE "{value}")\n'
                    'configure_file(value.in generated/value.txt @ONLY)\n'
                    'add_custom_target(CONFIG_INC DEPENDS "${CMAKE_BINARY_DIR}/generated/value.txt")\n'
                    'add_custom_target(IR_INC DEPENDS "${CMAKE_BINARY_DIR}/generated/value.txt")\n'
                )

            cmake_lists = source / "CMakeLists.txt"
            cmake_lists.write_text(project("before"), encoding="utf-8")
            subprocess.run(
                ["cmake", "-S", str(source), "-B", str(build), "-G", "Ninja"],
                check=True,
                capture_output=True,
                text=True,
            )
            counter = build / "configure-count.txt"
            generated = build / "generated/value.txt"
            self.assertEqual(counter.read_text(encoding="utf-8"), "x")

            with mock.patch.object(dev_build, "required_tool", return_value="cmake"):
                command = dev_build.editor_prerequisites_command(build)
            subprocess.run(command, check=True, capture_output=True, text=True)
            self.assertEqual(counter.read_text(encoding="utf-8"), "x")

            cmake_lists.write_text(project("after"), encoding="utf-8")
            subprocess.run(command, check=True, capture_output=True, text=True)
            self.assertEqual(counter.read_text(encoding="utf-8"), "xx")
            self.assertEqual(generated.read_text(encoding="utf-8"), "after\n")

            ordinary_source.write_text("int known = 1;\n", encoding="utf-8")
            subprocess.run(command, check=True, capture_output=True, text=True)
            self.assertEqual(counter.read_text(encoding="utf-8"), "xx")

    def test_build_requires_one_explicit_target(self):
        with mock.patch.object(dev_build, "required_tool", return_value="/tool/cmake"):
            command = dev_build.build_command(Path("/view/build"), "vulkan-host-64", 8)
            with self.assertRaises(ValueError):
                dev_build.build_command(Path("/view/build"), "--all", 8)

        self.assertEqual(command[-4:], ["--target", "vulkan-host-64", "--parallel", "8"])

    def test_compile_parser_and_unique_configured_object(self):
        args = dev_build.parser().parse_args(
            ["compile", "ThunkLibs/Generator/analysis.cpp", "--jobs", "4"]
        )
        self.assertEqual(
            (args.action, args.file, args.jobs),
            ("compile", Path("ThunkLibs/Generator/analysis.cpp"), 4),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source_file = source / "Source" / "unit.cpp"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("int unit;\n", encoding="utf-8")
            lane = root / "lane"
            build = lane / "build"
            build.mkdir(parents=True)
            os.symlink(source, lane / "src", target_is_directory=True)
            object_output = build / "Source" / "CMakeFiles" / "unit.dir" / "unit.cpp.o"
            database = [
                {
                    "directory": str(build),
                    "command": "clang++ -c unit.cpp",
                    "file": str(lane / "src" / "Source" / "unit.cpp"),
                    "output": str(object_output),
                }
            ]
            (build / "compile_commands.json").write_text(
                json.dumps(database), encoding="utf-8"
            )

            unit = dev_build.configured_compile_unit(
                source.resolve(), build, Path("Source/unit.cpp")
            )

        self.assertEqual(unit["sourceFile"], "Source/unit.cpp")
        self.assertEqual(
            unit["objectTarget"], "Source/CMakeFiles/unit.dir/unit.cpp.o"
        )
        self.assertEqual(unit["databaseEntries"], 1)
        self.assertRegex(unit["databaseSha256"], r"^[0-9a-f]{64}$")

    def test_configured_compile_unit_refuses_non_source_and_ambiguous_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            source_file = source / "unit.cpp"
            header = source / "unit.h"
            source_file.write_text("int unit;\n", encoding="utf-8")
            header.write_text("extern int unit;\n", encoding="utf-8")
            outside = root / "outside.cpp"
            outside.write_text("int outside;\n", encoding="utf-8")
            linked = source / "linked.cpp"
            linked.symlink_to(source_file)
            lane = root / "lane"
            build = lane / "build"
            build.mkdir(parents=True)
            os.symlink(source, lane / "src", target_is_directory=True)

            def write_database(outputs):
                entries = [
                    {
                        "directory": str(build),
                        "file": str(lane / "src" / "unit.cpp"),
                        "output": str(output),
                    }
                    for output in outputs
                ]
                (build / "compile_commands.json").write_text(
                    json.dumps(entries), encoding="utf-8"
                )

            write_database([build / "one.o"])
            with self.assertRaisesRegex(RuntimeError, "no configured compile command"):
                dev_build.configured_compile_unit(source.resolve(), build, header)
            with self.assertRaisesRegex(RuntimeError, "inside the selected source tree"):
                dev_build.configured_compile_unit(source.resolve(), build, outside)
            with self.assertRaisesRegex(RuntimeError, "regular file"):
                dev_build.configured_compile_unit(source.resolve(), build, linked)

            write_database([build / "one.o", build / "two.o"])
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                dev_build.configured_compile_unit(source.resolve(), build, source_file)

            write_database([root / "escaped.o"])
            with self.assertRaisesRegex(RuntimeError, "escapes"):
                dev_build.configured_compile_unit(source.resolve(), build, source_file)

    def test_compile_action_emits_exact_object_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source_file = source / "Source" / "unit.cpp"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("int unit;\n", encoding="utf-8")
            cache = root / "cache"
            lane = cache / "views" / "focused"
            build = lane / "build"
            build.mkdir(parents=True)
            os.symlink(source, lane / "src", target_is_directory=True)
            (build / "build.ninja").write_text("", encoding="utf-8")
            object_target = "Source/CMakeFiles/unit.dir/unit.cpp.o"
            (build / "compile_commands.json").write_text(
                json.dumps(
                    [
                        {
                            "directory": str(build),
                            "file": str(lane / "src" / "Source" / "unit.cpp"),
                            "output": str(build / object_target),
                        }
                    ]
                ),
                encoding="utf-8",
            )
            profile = dev_build.expected_profile("namespace")
            dev_build.write_receipt(lane / "profile.json", profile)
            output = __import__("io").StringIO()
            completed = subprocess.CompletedProcess(["cmake", "--build"], 0)
            empty_stats = {
                "cache_miss": 0,
                "compile_failed": 0,
                "direct_cache_hit": 0,
                "preprocessed_cache_hit": 0,
                "max_cache_size_kibibyte": 1024,
                "stats_updated_timestamp": 123,
            }
            build_environment = {
                "CCACHE_NAMESPACE": "namespace",
                "CCACHE_SLOPPINESS": "time_macros",
            }

            def run(command, **kwargs):
                if "--print-log-stats" in command:
                    return subprocess.CompletedProcess(
                        command, 0, stdout=json.dumps(empty_stats), stderr=""
                    )
                stats_log = Path(kwargs["env"]["CCACHE_STATSLOG"])
                self.assertTrue(stats_log.is_file())
                self.assertEqual(stat.S_IMODE(stats_log.stat().st_mode), 0o600)
                return completed

            with mock.patch.object(
                dev_build,
                "source_preflight",
                return_value={
                    "head": "a" * 40,
                    "dirty": True,
                    "submoduleValidation": {},
                },
            ):
                with mock.patch.object(
                    dev_build, "required_tool", side_effect=lambda name: f"/tool/{name}"
                ):
                    with mock.patch.object(
                        dev_build,
                        "environment",
                        return_value=build_environment,
                    ):
                        with mock.patch.object(dev_build, "git_output", return_value="a" * 40):
                            with mock.patch.object(
                                dev_build, "configured_provenance_matches", return_value=True
                            ):
                                with mock.patch.object(
                                    dev_build,
                                    "source_identity",
                                    return_value={"head": "a" * 40, "dirty": True},
                                ):
                                    with mock.patch.object(
                                        dev_build.subprocess, "run", side_effect=run
                                    ) as run_mock:
                                        with mock.patch("sys.stdout", output):
                                            result = dev_build.main(
                                                [
                                                    "--source",
                                                    str(source),
                                                    "--cache-root",
                                                    str(cache),
                                                    "--lane",
                                                    "focused",
                                                    "compile",
                                                    "Source/unit.cpp",
                                                    "--jobs",
                                                    "4",
                                                ]
                                            )

            receipt = json.loads(output.getvalue().splitlines()[-1])
            stored = json.loads((lane / "last-receipt.json").read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(receipt, stored)
        self.assertEqual(receipt["format"], "teamleaderleo-fex-x86-host-compile-receipt-v1")
        self.assertEqual(receipt["sourceFile"], "Source/unit.cpp")
        self.assertEqual(receipt["objectTarget"], object_target)
        self.assertEqual(receipt["head"], "a" * 40)
        self.assertTrue(receipt["dirty"])
        self.assertEqual(receipt["exitCode"], 0)
        self.assertEqual(receipt["cacheObservation"]["result"], "not_invoked")
        self.assertFalse(receipt["cacheObservation"]["rawLogRetained"])
        self.assertNotIn("CCACHE_STATSLOG", build_environment)
        build_calls = [
            call for call in run_mock.call_args_list if "--print-log-stats" not in call.args[0]
        ]
        self.assertEqual(len(build_calls), 1)
        command = build_calls[0].args[0]
        self.assertEqual(command[-4:], ["--target", object_target, "--parallel", "4"])
        self.assertFalse(any(lane.glob(".compile-cache-*")))

    def test_ccache_stats_json_classifies_outcomes_and_preserves_events(self):
        base = {
            "cache_miss": 0,
            "compile_failed": 0,
            "direct_cache_hit": 0,
            "preprocessed_cache_hit": 0,
            "max_cache_size_kibibyte": 1024,
            "stats_updated_timestamp": 123,
        }

        def classify(**updates):
            counters = {**base, **updates}
            return dev_build.parse_ccache_stats_json(json.dumps(counters))

        self.assertEqual(classify()["result"], "not_invoked")
        direct = classify(direct_cache_hit=1, local_storage_hit=1)
        self.assertEqual(direct["result"], "direct_hit")
        self.assertEqual(direct["eventCounters"]["local_storage_hit"], 1)
        self.assertNotIn("max_cache_size_kibibyte", direct["eventCounters"])
        self.assertEqual(classify(preprocessed_cache_hit=1)["result"], "preprocessed_hit")
        self.assertEqual(classify(cache_miss=1)["result"], "cache_miss")
        self.assertEqual(classify(compile_failed=1)["result"], "compile_failed")
        self.assertEqual(classify(called_for_link=1)["result"], "uncacheable")
        multiple = classify(direct_cache_hit=1, cache_miss=1)
        self.assertEqual(multiple["result"], "multiple_invocations")
        self.assertEqual(multiple["cacheableCalls"], 2)

    def test_ccache_stats_json_refuses_malformed_oversized_and_invalid_counters(self):
        core = {
            "cache_miss": 0,
            "compile_failed": 0,
            "direct_cache_hit": 0,
            "preprocessed_cache_hit": 0,
        }
        invalid = (
            "not json",
            "[]",
            json.dumps({"cache_miss": 0}),
            json.dumps({**core, "bad": -1}),
            json.dumps({**core, "bad": True}),
        )
        for payload in invalid:
            with self.subTest(payload=payload[:40]):
                with self.assertRaises(RuntimeError):
                    dev_build.parse_ccache_stats_json(payload)
        with mock.patch.object(dev_build, "CCACHE_STATS_OUTPUT_LIMIT", 8):
            with self.assertRaisesRegex(RuntimeError, "bounded"):
                dev_build.parse_ccache_stats_json(json.dumps(core))

    def test_compile_cache_observation_is_private_isolated_and_deleted(self):
        with tempfile.TemporaryDirectory() as temporary:
            lane = Path(temporary)
            caller_env = {"CCACHE_STATSLOG": "/caller/log", "KEEP": "yes"}
            calls = []

            def run(command, **kwargs):
                calls.append((command, kwargs))
                stats_log = Path(kwargs["env"]["CCACHE_STATSLOG"])
                self.assertTrue(stats_log.is_file())
                if "--print-log-stats" in command:
                    counters = {
                        "cache_miss": int(stats_log.stat().st_size > 0),
                        "compile_failed": 0,
                        "direct_cache_hit": 0,
                        "preprocessed_cache_hit": 0,
                    }
                    return subprocess.CompletedProcess(
                        command, 0, stdout=json.dumps(counters), stderr=""
                    )
                stats_log.write_text("# /private/source.cpp\ncache_miss\n", encoding="utf-8")
                os.chmod(stats_log, 0o600)
                return subprocess.CompletedProcess(command, 0)

            with mock.patch.object(dev_build, "required_tool", return_value="/tool/ccache"):
                completed, observation = dev_build.run_compile_with_cache_observation(
                    ["/tool/cmake", "--build"], caller_env, lane, run
                )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(observation["result"], "cache_miss")
            self.assertEqual(caller_env, {"CCACHE_STATSLOG": "/caller/log", "KEEP": "yes"})
            self.assertEqual(len(calls), 3)
            private_path = Path(calls[0][1]["env"]["CCACHE_STATSLOG"])
            self.assertNotEqual(private_path, Path("/caller/log"))
            self.assertFalse(private_path.exists())
            self.assertFalse(any(lane.glob(".compile-cache-*")))

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

            with mock.patch.object(
                dev_build,
                "source_preflight",
                return_value={
                    "head": "a" * 40,
                    "dirty": True,
                    "submoduleValidation": {},
                },
            ):
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

        check_set = dev_build.parser().parse_args(["check-set", "thunkgentest"])
        self.assertEqual((check_set.action, check_set.target), ("check-set", "thunkgentest"))
        for invalid in ("", "../tool", "/tool", "two targets", "all|other"):
            with self.assertRaises(ValueError):
                dev_build.validate_target(invalid)

    def test_ninja_query_binds_one_exact_private_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary).resolve()
            binary = build / "Bin" / "thunkgentest"
            binary.parent.mkdir()
            binary.write_bytes(b"binary")
            binary.chmod(0o700)
            payload = "thunkgentest:\n  input: phony\n    Bin/thunkgentest\n  outputs:\n"
            artifact = dev_build.configured_target_artifact(
                build, "thunkgentest", payload
            )
            self.assertEqual(artifact, binary)

            for rejected in (
                "thunkgentest:\n  input: phony\n  outputs:\n",
                "thunkgentest:\n  input: phony\n    Bin/a\n    Bin/b\n  outputs:\n",
                "thunkgentest:\n  input: CUSTOM_COMMAND\n    Bin/thunkgentest\n  outputs:\n",
                "other:\n  input: phony\n    Bin/thunkgentest\n  outputs:\n",
            ):
                with self.assertRaises(RuntimeError):
                    dev_build.configured_target_artifact(build, "thunkgentest", rejected)

            binary.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError, "not a private executable"):
                dev_build.configured_target_artifact(build, "thunkgentest", payload)
            binary.unlink()
            binary.symlink_to("other")
            (binary.parent / "other").write_bytes(b"binary")
            (binary.parent / "other").chmod(0o700)
            with self.assertRaisesRegex(RuntimeError, "not a private executable"):
                dev_build.configured_target_artifact(build, "thunkgentest", payload)

    def test_ninja_cooutput_selects_only_exact_generated_target_ctests(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary).resolve()
            artifact = build / "Bin" / "thunkgentest"
            artifact.parent.mkdir()
            artifact.write_bytes(b"binary")
            artifact.chmod(0o700)
            generated = build / "generated" / "target-tests.cmake"
            generated.parent.mkdir()
            generated.write_text(
                f"add_test( [==[B.Test]==] {artifact} [==[B.Test]==] )\n"
                "set_tests_properties( [==[B.Test]==] PROPERTIES SKIP_RETURN_CODE 4)\n"
                f"add_test( [==[A.Test]==] {artifact} [==[A.Test]==] )\n"
                "set_tests_properties( [==[A.Test]==] PROPERTIES SKIP_RETURN_CODE 4)\n"
                "set( thunkgentest_TESTS [==[B.Test]==] [==[A.Test]==] )\n",
                encoding="utf-8",
            )
            manifest = build / "build.ninja"
            manifest.write_text(
                "build Bin/thunkgentest generated/target-tests.cmake | "
                "${cmake_ninja_workdir}generated/target-tests.cmake: LINK object.o\n",
                encoding="utf-8",
            )
            selected = dev_build.generated_target_ctest_set(
                manifest, build, artifact
            )

            self.assertEqual(selected["selectedTests"], ["A.Test", "B.Test"])
            self.assertEqual(selected["artifactOutput"], "Bin/thunkgentest")
            self.assertEqual(selected["registrationFiles"], 1)
            self.assertRegex(selected["digest"], r"^[0-9a-f]{64}$")
            self.assertRegex(selected["buildManifestDigest"], r"^[0-9a-f]{64}$")
            with self.assertRaisesRegex(RuntimeError, "bounded limit"):
                dev_build.generated_target_ctest_set(
                    manifest, build, artifact, limit=1
                )

            generated.write_text(
                f"add_test(Wrapped.Test /wrapper {artifact})\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "owns no exact"):
                dev_build.generated_target_ctest_set(manifest, build, artifact)

    def test_target_ctest_cooutput_fails_closed_on_graph_and_grammar_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary).resolve()
            artifact = build / "Bin" / "test"
            artifact.parent.mkdir()
            artifact.write_bytes(b"binary")
            artifact.chmod(0o700)
            generated = build / "tests.cmake"
            generated.write_text(
                f"if(FALSE)\nadd_test(Hidden.Test {artifact})\nendif()\n",
                encoding="utf-8",
            )
            manifest = build / "build.ninja"
            manifest.write_text(
                "build Bin/test tests.cmake: LINK object.o\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "grammar"):
                dev_build.generated_target_ctest_set(manifest, build, artifact)

            manifest.write_text(
                "build Bin/test tests.cmake: LINK one.o\n"
                "build Bin/test other.cmake: LINK two.o\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "one exact Ninja producer"):
                dev_build.configured_target_cooutputs(manifest, build, artifact)

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
        self.assertEqual(
            dev_build.generated_ctest_definition(
                "add_test( [==[one test]==] /lane/Bin/test [==[one test]==] )"
            ),
            {"name": "one test", "commandHead": "/lane/Bin/test"},
        )
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

    def test_selected_ctest_crosscheck_counts_only_exact_add_test_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            (build / "target.cmake").write_text(
                "add_test( [=[A.Test]=] /bin/target [=[A.Test]=] )\n"
                "set_tests_properties( [=[A.Test]=] PROPERTIES SKIP_RETURN_CODE 4)\n"
                "set(target_TESTS [=[A.Test]=])\n",
                encoding="utf-8",
            )
            (build / "unrelated.cmake").write_text(
                "add_test(Unrelated.Test /bin/other)\n", encoding="utf-8"
            )
            receipt = dev_build.selected_generated_ctest_crosscheck(
                build, ["A.Test"]
            )
            self.assertEqual(receipt["scannedFiles"], 2)
            self.assertEqual(receipt["matchingFiles"], 1)
            self.assertEqual(receipt["matchingDefinitions"], 1)
            self.assertRegex(receipt["digest"], r"^[0-9a-f]{64}$")

            (build / "duplicate.cmake").write_text(
                "add_test(A.Test /bin/duplicate)\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "A.Test.*2"):
                dev_build.selected_generated_ctest_crosscheck(build, ["A.Test"])

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

    def test_porcelain_v2_status_selects_only_submodule_sensitive_fallbacks(self):
        head = "a" * 40
        clean = f"# branch.oid {head}\0# branch.head main\0".encode()
        ordinary = clean + (
            "1 .M N... 100644 100644 100644 "
            f"{'b' * 40} {'c' * 40} Source/unit.cpp\0"
        ).encode()
        submodule = clean + (
            "1 .M S.M. 160000 160000 160000 "
            f"{'b' * 40} {'c' * 40} External/fmt\0"
        ).encode()
        gitmodules = clean + (
            "1 .M N... 100644 100644 100644 "
            f"{'b' * 40} {'c' * 40} .gitmodules\0"
        ).encode()

        self.assertEqual(
            dev_build.parse_source_status(clean),
            {"head": head, "dirty": False, "submoduleFallback": False},
        )
        self.assertEqual(
            dev_build.parse_source_status(ordinary),
            {"head": head, "dirty": True, "submoduleFallback": False},
        )
        self.assertTrue(dev_build.parse_source_status(submodule)["submoduleFallback"])
        self.assertTrue(dev_build.parse_source_status(gitmodules)["submoduleFallback"])
        with self.assertRaisesRegex(RuntimeError, "omitted its origin"):
            dev_build.parse_source_status(
                clean
                + (
                    "2 R. N... 100644 100644 100644 "
                    f"{'b' * 40} {'c' * 40} R100 renamed.cpp\0"
                ).encode()
            )

    def test_index_gitlinks_are_exact_stage_zero_safe_paths(self):
        payload = (
            f"100644 {'a' * 40} 0\tREADME.md\0"
            f"160000 {'b' * 40} 0\tExternal/zeta\0"
            f"160000 {'c' * 40} 0\tExternal/alpha\0"
        ).encode()
        self.assertEqual(
            dev_build.parse_index_gitlinks(payload),
            [("External/alpha", "c" * 40), ("External/zeta", "b" * 40)],
        )
        with self.assertRaisesRegex(RuntimeError, "unresolved stages"):
            dev_build.parse_index_gitlinks(
                f"100644 {'a' * 40} 2\tordinary.cpp\0".encode()
            )
        with self.assertRaisesRegex(RuntimeError, "unsafe submodule path"):
            dev_build.parse_index_gitlinks(
                f"160000 {'a' * 40} 0\t../escaped\0".encode()
            )

    def test_authoritative_identity_visits_only_checked_in_recursive_maps(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            child = source / "child"
            grand = child / "grand"
            grand.mkdir(parents=True)
            (source / ".gitmodules").write_text("root\n", encoding="utf-8")
            (child / ".gitmodules").write_text("child\n", encoding="utf-8")
            module_root = root / "git" / "modules"
            module_root.mkdir(parents=True)
            child_pin = "a" * 40
            grand_pin = "b" * 40
            indexes = {
                source: ([("child", child_pin)], "c" * 40),
                child.resolve(): ([("grand", grand_pin)], "d" * 40),
            }
            configured = {
                source: ["child"],
                child.resolve(): ["grand"],
            }

            def heads(selected, _module_root):
                if selected == child.resolve():
                    return child_pin, module_root / "child"
                return grand_pin, module_root / "grand"

            with mock.patch.object(
                dev_build, "repository_git_dir", return_value=root / "git"
            ):
                with mock.patch.object(
                    dev_build, "index_submodules", side_effect=lambda path: indexes[path]
                ) as inspect_index:
                    with mock.patch.object(
                        dev_build,
                        "gitmodule_paths",
                        side_effect=lambda path, _blob: configured[path],
                    ):
                        with mock.patch.object(
                            dev_build, "detached_submodule_head", side_effect=heads
                        ):
                            count, digest = dev_build.authoritative_submodule_identity(source)

        expected = __import__("hashlib").sha256(
            f"{child_pin} child\n{grand_pin} child/grand\n".encode()
        ).hexdigest()
        self.assertEqual((count, digest), (2, expected))
        self.assertEqual(inspect_index.call_count, 2)

    def test_source_preflight_uses_authoritative_gitlinks_when_supported(self):
        with mock.patch.object(
            dev_build,
            "authoritative_submodule_identity",
            return_value=(18, "b" * 64),
        ):
            with mock.patch.object(dev_build, "require_pinned_submodules") as require:
                with mock.patch.object(dev_build, "git_output", return_value="a" * 40):
                    receipt = dev_build.source_preflight(Path("/worktree"))

        require.assert_not_called()
        self.assertEqual(receipt["head"], "a" * 40)
        self.assertEqual(receipt["submoduleValidation"]["mode"], "authoritative_gitlinks")

    def test_source_preflight_does_not_accept_fast_path_safety_errors(self):
        with mock.patch.object(
            dev_build,
            "authoritative_submodule_identity",
            side_effect=RuntimeError("unsafe Gitdir"),
        ):
            with mock.patch.object(dev_build, "require_pinned_submodules") as require:
                with mock.patch.object(dev_build, "git_output", return_value="a" * 40):
                    with self.assertRaisesRegex(RuntimeError, "unsafe Gitdir"):
                        dev_build.source_preflight(Path("/worktree"))

        require.assert_called_once_with(Path("/worktree"))

    def test_source_preflight_uses_recursive_compatibility_for_supported_layout(self):
        with mock.patch.object(
            dev_build,
            "authoritative_submodule_identity",
            side_effect=dev_build.UnsupportedSubmoduleLayout("symbolic HEAD"),
        ):
            with mock.patch.object(dev_build, "require_pinned_submodules"):
                with mock.patch.object(
                    dev_build,
                    "pinned_submodule_identity",
                    return_value=(18, "b" * 64),
                ):
                    with mock.patch.object(dev_build, "git_output", return_value="a" * 40):
                        receipt = dev_build.source_preflight(Path("/worktree"))

        self.assertEqual(
            receipt["submoduleValidation"]["mode"],
            "recursive_porcelain_fallback",
        )

    def test_source_preflight_refuses_a_head_change_during_observation(self):
        with mock.patch.object(
            dev_build,
            "authoritative_submodule_identity",
            return_value=(18, "b" * 64),
        ):
            with mock.patch.object(
                dev_build, "git_output", side_effect=("a" * 40, "c" * 40)
            ):
                with self.assertRaisesRegex(RuntimeError, "HEAD changed"):
                    dev_build.source_preflight(Path("/worktree"))

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

    @staticmethod
    def retirement_source(root: Path) -> tuple[Path, str]:
        source = root / "source"
        source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(
            ["git", "-C", str(source), "config", "user.name", "FEX test"], check=True
        )
        subprocess.run(
            ["git", "-C", str(source), "config", "user.email", "fex@example.invalid"],
            check=True,
        )
        (source / "owned.txt").write_text("owned\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(source), "add", "owned.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(source), "commit", "-q", "-m", "fixture"], check=True
        )
        head = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return source, head

    @staticmethod
    def retirement_lane(
        cache: Path,
        root: Path,
        lane: str,
        head: str,
        *,
        dirty: bool = False,
        exit_code: int = 0,
        live: bool = False,
        receipt_format: str = "teamleaderleo-fex-x86-host-dev-receipt-v1",
    ) -> Path:
        lane_root = cache / "views" / lane
        (lane_root / "build").mkdir(parents=True)
        (cache / "locks").mkdir(parents=True, exist_ok=True)
        target = root / f"{lane}-source"
        if live:
            target.mkdir()
        os.symlink(target, lane_root / "src", target_is_directory=True)
        (lane_root / "build" / "build.ninja").write_text("# fixture\n", encoding="utf-8")
        dev_build.write_receipt(
            lane_root / "last-receipt.json",
            {
                "format": receipt_format,
                "head": head,
                "dirty": dirty,
                "target": "FEX",
                "exitCode": exit_code,
            },
        )
        dev_build.write_receipt(
            lane_root / "profile.json",
            dev_build.expected_profile(dev_build.cpu_namespace()),
        )
        return lane_root

    @classmethod
    def check_set_retirement_lane(
        cls, cache: Path, root: Path, lane: str, head: str
    ) -> tuple[Path, dict[str, object]]:
        lane_root = cls.retirement_lane(
            cache,
            root,
            lane,
            head,
            receipt_format="teamleaderleo-fex-x86-host-check-set-receipt-v2",
        )
        artifact = lane_root / "build" / "FEXCore_Tests" / "RetirementFixture"
        artifact.parent.mkdir()
        artifact.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        artifact.chmod(0o755)
        profile = json.loads((lane_root / "profile.json").read_text(encoding="utf-8"))
        receipt = {
            "format": "teamleaderleo-fex-x86-host-check-set-receipt-v2",
            "profile": profile["profile"],
            "lane": lane,
            "target": "RetirementFixture",
            "targetArtifact": str(artifact),
            "selectedTests": ["Alpha.Case", "Beta.Case"],
            "selectedTestCount": 2,
            "targetTestRegistry": {
                "artifactOutput": "FEXCore_Tests/RetirementFixture",
                "buildManifestDigest": "a" * 64,
                "digest": "b" * 64,
                "registrationFiles": 1,
                "registrationBytes": 256,
            },
            "selectedTestCrosscheck": {
                "digest": "c" * 64,
                "matchingFiles": 1,
                "matchingDefinitions": 2,
                "scannedFiles": 4,
                "scannedBytes": 4096,
            },
            "selectionLimit": dev_build.EXACT_CTEST_SET_LIMIT,
            "head": head,
            "dirty": False,
            "exitCode": 0,
            "cacheNamespace": profile["cacheNamespace"],
            "ccacheSloppiness": profile["ccacheSloppiness"],
        }
        dev_build.write_receipt(lane_root / "last-receipt.json", receipt)
        return lane_root, receipt

    def test_retirement_plan_is_deterministic_read_only_and_commit_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            source, head = self.retirement_source(root)
            lane_root = self.retirement_lane(cache, root, "dead-clean", head)
            before = sorted(
                (path.relative_to(lane_root).as_posix(), os.lstat(path).st_size)
                for path in lane_root.rglob("*")
            )

            first = dev_build.lane_retirement_plan(cache, "dead-clean", source)
            second = dev_build.lane_retirement_plan(cache, "dead-clean", source)
            after = sorted(
                (path.relative_to(lane_root).as_posix(), os.lstat(path).st_size)
                for path in lane_root.rglob("*")
            )

        self.assertEqual(first, second)
        self.assertEqual(before, after)
        self.assertEqual(first["status"], "ready")
        self.assertEqual(first["authority"], "read_only_plan")
        self.assertEqual(first["blockers"], [])
        self.assertEqual(first["receipt"]["head"], head)
        self.assertTrue(first["receiptHeadReachable"])
        self.assertRegex(first["retirementToken"], r"^[0-9a-f]{64}$")
        self.assertGreater(first["laneIdentity"]["entries"], 1)

    def test_retirement_plan_accepts_complete_check_set_v2_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            source, head = self.retirement_source(root)
            lane_root, _ = self.check_set_retirement_lane(
                cache, root, "check-set", head
            )
            before = (lane_root / "last-receipt.json").read_bytes()

            plan = dev_build.lane_retirement_plan(cache, "check-set", source)

            after = (lane_root / "last-receipt.json").read_bytes()

        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["blockers"], [])
        self.assertRegex(plan["retirementToken"], r"^[0-9a-f]{64}$")
        self.assertEqual(before, after)

    def test_retirement_plan_refuses_incomplete_or_unsafe_check_set_v2_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            source, head = self.retirement_source(root)
            plans = {}
            expected_blockers = {
                "bad-lane": "receipt_check_set_schema_invalid",
                "bad-target": "receipt_check_set_target_invalid",
                "bad-selection": "receipt_check_set_selection_invalid",
                "bad-selection-type": "receipt_check_set_selection_invalid",
                "bad-registry": "receipt_check_set_registry_invalid",
                "bad-crosscheck": "receipt_check_set_crosscheck_invalid",
                "bad-profile": "receipt_check_set_profile_invalid",
                "linked-artifact": "receipt_check_set_artifact_unsafe",
            }
            for lane, blocker in expected_blockers.items():
                lane_root, receipt = self.check_set_retirement_lane(
                    cache, root, lane, head
                )
                if lane == "bad-lane":
                    receipt["lane"] = "some-other-lane"
                elif lane == "bad-target":
                    receipt["target"] = "../RetirementFixture"
                elif lane == "bad-selection":
                    receipt["selectedTestCount"] = 1
                elif lane == "bad-selection-type":
                    receipt["selectedTests"] = [{"not": "a test name"}]
                elif lane == "bad-registry":
                    receipt["targetTestRegistry"]["digest"] = "not-a-digest"
                elif lane == "bad-crosscheck":
                    receipt["selectedTestCrosscheck"]["matchingDefinitions"] = 1
                elif lane == "bad-profile":
                    receipt["cacheNamespace"] = "foreign-cache"
                elif lane == "linked-artifact":
                    artifact = Path(receipt["targetArtifact"])
                    artifact.unlink()
                    outside = root / "outside-artifact"
                    outside.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    outside.chmod(0o755)
                    artifact.symlink_to(outside)
                dev_build.write_receipt(lane_root / "last-receipt.json", receipt)
                plans[lane] = dev_build.lane_retirement_plan(cache, lane, source)

        for lane, blocker in expected_blockers.items():
            with self.subTest(lane=lane):
                self.assertEqual(plans[lane]["status"], "refused")
                self.assertIn(blocker, plans[lane]["blockers"])
                self.assertIsNone(plans[lane]["retirementToken"])

    def test_retirement_plan_names_dirty_live_active_unreachable_and_unsafe_vetoes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            source, head = self.retirement_source(root)
            self.retirement_lane(cache, root, "dirty", head, dirty=True)
            self.retirement_lane(cache, root, "failed", head, exit_code=1)
            self.retirement_lane(cache, root, "live", head, live=True)
            self.retirement_lane(cache, root, "active", head)
            self.retirement_lane(cache, root, "unreachable", "f" * 40)
            self.retirement_lane(
                cache, root, "unknown-format", head, receipt_format="foreign-receipt"
            )
            bad_profile = self.retirement_lane(cache, root, "unknown-profile", head)
            dev_build.write_receipt(
                bad_profile / "profile.json", {"format": "foreign-profile"}
            )

            outside = root / "outside-lane"
            outside.mkdir()
            os.symlink(outside, cache / "views" / "linked", target_is_directory=True)

            active_lock = (cache / "locks" / "active.lock").open("a+", encoding="utf-8")
            fcntl = __import__("fcntl")
            fcntl.flock(active_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            plans = {
                lane: dev_build.lane_retirement_plan(cache, lane, source)
                for lane in (
                    "dirty",
                    "failed",
                    "live",
                    "active",
                    "unreachable",
                    "unknown-format",
                    "unknown-profile",
                    "linked",
                )
            }
            fcntl.flock(active_lock, fcntl.LOCK_UN)
            active_lock.close()

        self.assertIn("receipt_dirty_or_unknown", plans["dirty"]["blockers"])
        self.assertIsNone(plans["dirty"]["retirementToken"])
        self.assertIn("receipt_unsuccessful_or_unknown", plans["failed"]["blockers"])
        self.assertIn("source_view_live", plans["live"]["blockers"])
        self.assertIn("lane_lock_active", plans["active"]["blockers"])
        self.assertIn("receipt_head_unreachable", plans["unreachable"]["blockers"])
        self.assertIn("receipt_format_unknown", plans["unknown-format"]["blockers"])
        self.assertIn("profile_contract_unknown", plans["unknown-profile"]["blockers"])
        self.assertIn("lane_unsafe", plans["linked"]["blockers"])
        self.assertIsNone(plans["linked"]["retirementToken"])
        self.assertTrue(all(plan["status"] == "refused" for plan in plans.values()))

    def test_retirement_plan_refuses_observation_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            source, head = self.retirement_source(root)
            self.retirement_lane(cache, root, "changing", head)
            stable = dev_build.lane_inventory(cache)
            changed = json.loads(json.dumps(stable))
            changed["lanes"][0]["allocatedBytes"] += 4096
            with mock.patch.object(
                dev_build, "lane_inventory", side_effect=[stable, changed]
            ):
                plan = dev_build.lane_retirement_plan(cache, "changing", source)

        self.assertEqual(plan["status"], "refused")
        self.assertIn("lane_changed_during_plan", plan["blockers"])

    def test_retire_plan_missing_lane_creates_no_cache_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "missing-cache"
            source, _ = self.retirement_source(root)
            output = __import__("io").StringIO()
            with mock.patch("sys.stdout", output):
                result = dev_build.main(
                    [
                        "--lane",
                        "missing",
                        "--source",
                        str(source),
                        "--cache-root",
                        str(cache),
                        "retire-plan",
                    ]
                )

        plan = json.loads(output.getvalue())
        self.assertEqual(result, 2)
        self.assertEqual(plan["blockers"], ["lane_missing"])
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

    def test_same_worktree_stale_provenance_uses_bounded_refresh(self):
        self.assertEqual(
            dev_build.configuration_mode(
                "build",
                switched=False,
                build_configured=True,
                profile_compatible=True,
                provenance_compatible=False,
            ),
            "provenance-refresh",
        )

    def test_configured_git_hash_accepts_only_one_exact_owned_header(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            generated = build / "generated"
            generated.mkdir()
            header = generated / "git_version.h"
            expected = "0123456789abcdef0123456789abcdef01234567"
            fields = ", ".join(
                f"0x{expected[index:index + 2]}" for index in range(0, len(expected), 2)
            )
            header.write_text(
                "#pragma once\n"
                f"static constexpr std::array<uint8_t, 20> GIT_HASH = {{{fields}, }};\n",
                encoding="ascii",
            )

            self.assertEqual(dev_build.configured_git_hash(build), expected)
            self.assertTrue(dev_build.configured_provenance_matches(build, expected.upper()))
            header.write_text(
                "static constexpr std::array<uint8_t, 20> GIT_HASH = {0x01};\n",
                encoding="ascii",
            )
            self.assertIsNone(dev_build.configured_git_hash(build))
            self.assertFalse(dev_build.configured_provenance_matches(build, expected))
            duplicate = (
                f"static constexpr std::array<uint8_t, 20> GIT_HASH = {{{fields}, }};\n"
            )
            header.write_text(duplicate + duplicate, encoding="ascii")
            self.assertIsNone(dev_build.configured_git_hash(build))

    def test_missing_configured_provenance_is_not_a_warm_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            self.assertIsNone(dev_build.configured_git_hash(build))
            self.assertFalse(dev_build.configured_provenance_matches(build, "a" * 40))

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
