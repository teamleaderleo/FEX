#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
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

    def test_lane_names_cannot_escape_cache_root(self):
        self.assertEqual(dev_build.validate_lane("callback-fix.2"), "callback-fix.2")
        for invalid in ("../other", "/tmp/other", "two lanes", ""):
            with self.assertRaises(ValueError):
                dev_build.validate_lane(invalid)

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
