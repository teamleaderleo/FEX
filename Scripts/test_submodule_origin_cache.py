#!/usr/bin/env python3
"""Focused tests for the origin-bound shallow-submodule seed cache."""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import SubmoduleOriginCache as origin_cache


def git(*arguments: str, **kwargs) -> subprocess.CompletedProcess:
    check = kwargs.pop("check", True)
    return subprocess.run(
        ["git", *arguments], check=check, text=True, capture_output=True, **kwargs
    )


class SubmoduleOriginCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote"
        self.superproject = self.root / "superproject"
        self.cache = self.root / "cache"
        self.canonical = "https://example.invalid/dependency.git"
        self._init_repository(self.remote)
        (self.remote / "payload.txt").write_text("payload\n", encoding="utf-8")
        git("-C", str(self.remote), "add", "payload.txt")
        git("-C", str(self.remote), "commit", "-m", "payload")
        self.pin = git("-C", str(self.remote), "rev-parse", "HEAD").stdout.strip()

        self._init_repository(self.superproject)
        git(
            "-c",
            "protocol.file.allow=always",
            "-C",
            str(self.superproject),
            "submodule",
            "add",
            self.remote.as_uri(),
            "External/dependency",
        )
        git(
            "-C",
            str(self.superproject),
            "config",
            "-f",
            ".gitmodules",
            "submodule.External/dependency.url",
            self.canonical,
        )
        git("-C", str(self.superproject), "add", ".gitmodules", "External/dependency")
        git("-C", str(self.superproject), "commit", "-m", "add dependency")

    @staticmethod
    def _init_repository(path: Path) -> None:
        git("init", "--quiet", "--initial-branch=main", str(path))
        git("-C", str(path), "config", "user.name", "Test")
        git("-C", str(path), "config", "user.email", "test@example.invalid")

    def clone_superproject(self, name: str, *, cold_rewrite: bool) -> Path:
        destination = self.root / name
        git("clone", "--quiet", "--no-local", str(self.superproject), str(destination))
        if cold_rewrite:
            git(
                "-C",
                str(destination),
                "config",
                "protocol.file.allow",
                "always",
            )
            git(
                "-C",
                str(destination),
                "config",
                f"url.{self.remote.as_uri()}.insteadOf",
                self.canonical,
            )
        return destination

    def cold_update(self, source: Path) -> dict[str, object]:
        environment = {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "protocol.file.allow",
            "GIT_CONFIG_VALUE_0": "always",
            "GIT_CONFIG_KEY_1": f"url.{self.remote.as_uri()}.insteadOf",
            "GIT_CONFIG_VALUE_1": self.canonical,
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            return origin_cache.update(source, self.cache, 2)

    def test_cold_population_then_warm_self_contained_hit(self) -> None:
        first = self.clone_superproject("first", cold_rewrite=True)
        cold = self.cold_update(first)
        self.assertEqual(cold["state"], "cold_populated")
        self.assertEqual(cold["fetchedPins"], 1)
        self.assertEqual(cold["localOrigins"], 0)
        self.assertEqual(cold["alternates"], 0)
        inventory = origin_cache.inventory(self.cache)
        self.assertEqual(inventory["totals"]["generations"], 1)
        self.assertEqual(inventory["totals"]["origins"], 1)
        self.assertEqual(inventory["totals"]["advertisedPins"], 1)
        self.assertGreater(inventory["totals"]["allocatedBytes"], 0)

        second = self.clone_superproject("second", cold_rewrite=False)
        shutil.rmtree(self.remote)
        warm = origin_cache.update(second, self.cache, 2)
        self.assertEqual(warm["state"], "warm_hit")
        self.assertEqual(warm["localOrigins"], 1)
        self.assertEqual(warm["recursivePinDigest"], cold["recursivePinDigest"])
        dependency = second / "External/dependency"
        self.assertEqual(git("-C", str(dependency), "rev-parse", "HEAD").stdout.strip(), self.pin)
        self.assertEqual(
            git("-C", str(dependency), "config", "--get", "remote.origin.url").stdout.strip(),
            self.canonical,
        )
        git("-C", str(dependency), "fsck", "--full", "--no-reflogs")
        gitdir = Path(
            git("-C", str(dependency), "rev-parse", "--absolute-git-dir").stdout.strip()
        )
        self.assertFalse((gitdir / "objects/info/alternates").exists())
        self.assertEqual(
            git("-C", str(second), "config", "--get-regexp", "^url\\.", check=False).returncode,
            1,
        )

    def test_insecure_top_level_origin_is_refused_before_update(self) -> None:
        consumer = self.clone_superproject("consumer", cold_rewrite=True)
        git(
            "-C",
            str(consumer),
            "config",
            "-f",
            ".gitmodules",
            "submodule.External/dependency.url",
            self.remote.as_uri(),
        )
        with self.assertRaisesRegex(RuntimeError, "absolute HTTPS"):
            origin_cache.update(consumer, self.cache, 2)
        self.assertFalse((consumer / "External/dependency/payload.txt").exists())

    def test_empty_inventory_does_not_create_cache(self) -> None:
        missing = self.root / "missing-cache"
        self.assertEqual(origin_cache.inventory(missing)["totals"]["origins"], 0)
        self.assertFalse(missing.exists())

    def test_one_origin_repository_advertises_two_exact_shallow_pins(self) -> None:
        first_pin = self.pin
        (self.remote / "second.txt").write_text("second\n", encoding="utf-8")
        git("-C", str(self.remote), "add", "second.txt")
        git("-C", str(self.remote), "commit", "-m", "second")
        second_pin = git("-C", str(self.remote), "rev-parse", "HEAD").stdout.strip()
        sources = []
        for name, pin in (("pin-a", first_pin), ("pin-b", second_pin)):
            source = self.root / name
            git("clone", "--quiet", "--shared", str(self.remote), str(source))
            git("-C", str(source), "checkout", "--detach", "--quiet", pin)
            sources.append(
                {
                    "path": f"External/{name}",
                    "pin": pin,
                    "origin": self.canonical,
                    "originId": __import__("hashlib").sha256(
                        self.canonical.encode()
                    ).hexdigest(),
                    "gitdir": Path(
                        git(
                            "-C", str(source), "rev-parse", "--absolute-git-dir"
                        ).stdout.strip()
                    ),
                }
            )
        self.cache.mkdir(mode=0o700)
        root = origin_cache.cache_root(self.cache)
        root.mkdir(mode=0o700)
        origin_cache.origins_root(self.cache).mkdir(mode=0o700)
        fetched, reused = origin_cache.populate_origin(
            self.cache, self.canonical, sources
        )
        self.assertEqual((fetched, reused), (2, 0))
        origin_id = sources[0]["originId"]
        repository = origin_cache.validate_origin_repository(
            self.cache, self.canonical, origin_id
        )
        self.assertTrue(origin_cache.pin_ref_available(repository, first_pin))
        self.assertTrue(origin_cache.pin_ref_available(repository, second_pin))

    def test_missing_warm_seed_fails_instead_of_using_network(self) -> None:
        first = self.clone_superproject("first", cold_rewrite=True)
        cold = self.cold_update(first)
        origin_id = __import__("hashlib").sha256(self.canonical.encode()).hexdigest()
        shutil.rmtree(origin_cache.origin_directory(self.cache, origin_id))
        second = self.clone_superproject("second", cold_rewrite=False)
        with self.assertRaisesRegex(RuntimeError, "cannot inspect origin-cache directory"):
            origin_cache.update(second, self.cache, 2)
        self.assertEqual(cold["state"], "cold_populated")
        self.assertFalse((second / "External/dependency/payload.txt").exists())

    def test_inventory_refuses_manifest_pin_without_advertised_ref(self) -> None:
        first = self.clone_superproject("first", cold_rewrite=True)
        self.cold_update(first)
        origin_id = __import__("hashlib").sha256(self.canonical.encode()).hexdigest()
        repository = origin_cache.validate_origin_repository(
            self.cache, self.canonical, origin_id
        )
        git(
            f"--git-dir={repository}",
            "update-ref",
            "-d",
            f"refs/fex-pins/{self.pin}",
        )
        with self.assertRaisesRegex(RuntimeError, "unavailable advertised pins"):
            origin_cache.inventory(self.cache)

    def test_manifest_symlink_is_refused(self) -> None:
        first = self.clone_superproject("first", cold_rewrite=True)
        cold = self.cold_update(first)
        manifest = origin_cache.manifest_path(self.cache, cold["generation"])
        external = self.root / "external.json"
        shutil.copyfile(manifest, external)
        manifest.unlink()
        manifest.symlink_to(external)
        second = self.clone_superproject("second", cold_rewrite=False)
        with self.assertRaisesRegex(RuntimeError, "cannot open origin-cache metadata"):
            origin_cache.update(second, self.cache, 2)

    def test_inventory_lock_is_nonblocking(self) -> None:
        first = self.clone_superproject("first", cold_rewrite=True)
        self.cold_update(first)
        lock = self.cache / "locks/submodule-origins.lock"
        descriptor = os.open(lock, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        second = self.clone_superproject("second", cold_rewrite=False)
        with self.assertRaisesRegex(RuntimeError, "active"):
            origin_cache.update(second, self.cache, 2)

    def test_local_command_keeps_rewrites_command_scoped(self) -> None:
        command = origin_cache.local_update_command(
            Path("/worktree"), 4, {self.canonical: Path("/cache/repo.git")}
        )
        self.assertEqual(command[0:3], ["git", "-c", "protocol.file.allow=always"])
        self.assertIn(
            f"url.file:///cache/repo.git.insteadOf={self.canonical}", command
        )
        self.assertEqual(command[-4:], ["--depth", "1", "--jobs", "4"])


if __name__ == "__main__":
    unittest.main()
