#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
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

    def test_build_requires_one_explicit_target(self):
        with mock.patch.object(dev_build, "required_tool", return_value="/tool/cmake"):
            command = dev_build.build_command(Path("/view/build"), "vulkan-host-64", 8)
            with self.assertRaises(ValueError):
                dev_build.build_command(Path("/view/build"), "--all", 8)

        self.assertEqual(command[-4:], ["--target", "vulkan-host-64", "--parallel", "8"])

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
        inventory = dev_build.parser().parse_args(["submodule-cache"])
        self.assertEqual(inventory.action, "submodule-cache")

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

            with mock.patch.object(dev_build, "required_tool", return_value="/tool/cmake"):
                switched = dev_build.prepare_source_view(
                    new.resolve(), source_view, build, {}, runner=runner
                )

            self.assertTrue(switched)
            self.assertEqual(calls[0][2], old.resolve())
            self.assertEqual(calls[0][0][-1], "clean")
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

            with mock.patch.object(dev_build, "required_tool", return_value="/tool/cmake"):
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
