#!/usr/bin/env python3
"""Focused safety and accounting tests for the shallow-submodule pack cache."""

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import SubmodulePackCache as pack_cache


class SubmodulePackCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.modules = self.root / "modules"
        self.cache = self.root / "cache"
        self.generation = "a" * 64
        self.expected_contents: dict[Path, bytes] = {}
        for name in ("External/alpha", "External/beta"):
            repository = self.modules / name
            pack = repository / "objects" / "pack"
            pack.mkdir(parents=True)
            (repository / "config").write_text("[remote \"origin\"]\n", encoding="utf-8")
            (repository / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            for suffix, payload in (
                ("pack", b"pack payload\n"),
                ("idx", b"index payload\n"),
                ("rev", b"reverse index\n"),
            ):
                path = pack / f"pack-{'1' * 40}.{suffix}"
                path.write_bytes(payload)
                path.chmod(0o444)
                self.expected_contents[path] = payload

    def compact(self) -> dict[str, object]:
        with mock.patch.object(pack_cache, "module_root", return_value=self.modules):
            with mock.patch.object(pack_cache, "origin_digest", return_value="b" * 64):
                return pack_cache.compact(
                    Path("/synthetic/source"), self.cache, self.generation, 2
                )

    def pool_files(self) -> Path:
        return pack_cache.generation_root(self.cache, self.generation) / "files"

    def test_compaction_deduplicates_and_repeats_without_alternates(self) -> None:
        receipt = self.compact()
        self.assertEqual(receipt["sourceFiles"], 6)
        self.assertEqual(receipt["copiedEntries"], 3)
        self.assertEqual(receipt["linkedEntries"], 6)
        self.assertEqual(receipt["alreadyLinkedEntries"], 0)
        entries = list(self.pool_files().iterdir())
        self.assertEqual(len(entries), 3)

        pool_inodes = {(path.stat().st_dev, path.stat().st_ino) for path in entries}
        for path, payload in self.expected_contents.items():
            self.assertEqual(path.read_bytes(), payload)
            self.assertIn((path.stat().st_dev, path.stat().st_ino), pool_inodes)
            self.assertFalse((path.parent.parent / "info" / "alternates").exists())

        repeated = self.compact()
        self.assertEqual(repeated["copiedEntries"], 0)
        self.assertEqual(repeated["linkedEntries"], 0)
        self.assertEqual(repeated["alreadyLinkedEntries"], 6)

    def test_pool_unlink_does_not_remove_consumer_objects(self) -> None:
        self.compact()
        shutil.rmtree(pack_cache.pool_root(self.cache))
        for path, payload in self.expected_contents.items():
            self.assertEqual(path.read_bytes(), payload)

    def test_inventory_separates_linked_and_reclaimable_entries(self) -> None:
        self.compact()
        linked = pack_cache.inventory(self.cache)
        self.assertEqual(linked["totals"]["generations"], 1)
        self.assertEqual(linked["totals"]["entries"], 3)
        self.assertEqual(linked["totals"]["consumerLinks"], 6)
        self.assertGreater(linked["totals"]["linkedAllocatedBytes"], 0)
        self.assertEqual(linked["totals"]["reclaimableAllocatedBytes"], 0)

        shutil.rmtree(self.modules)
        reclaimable = pack_cache.inventory(self.cache)
        self.assertEqual(reclaimable["totals"]["consumerLinks"], 0)
        self.assertEqual(reclaimable["totals"]["linkedAllocatedBytes"], 0)
        self.assertGreater(reclaimable["totals"]["reclaimableAllocatedBytes"], 0)

    def test_empty_inventory_does_not_create_cache_state(self) -> None:
        missing = self.root / "missing-cache"
        inventory = pack_cache.inventory(missing)
        self.assertEqual(inventory["totals"]["generations"], 0)
        self.assertFalse(missing.exists())

    def test_inventory_refuses_concurrent_mutation(self) -> None:
        self.compact()
        lock = self.cache / "locks" / "submodule-packs.lock"
        descriptor = os.open(lock, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaisesRegex(RuntimeError, "active"):
            pack_cache.inventory(self.cache)

    def test_unsafe_pack_entries_fail_closed(self) -> None:
        bad = next(self.modules.rglob("objects/pack")) / "foreign.pack"
        external = self.root / "external.pack"
        external.write_bytes(b"foreign\n")
        bad.symlink_to(external)
        with self.assertRaisesRegex(RuntimeError, "unsafe"):
            self.compact()

    def test_writable_pack_entries_fail_closed(self) -> None:
        target = next(iter(self.expected_contents))
        target.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, "mode 0444"):
            self.compact()

    def test_foreign_hardlink_relationship_is_not_rewritten(self) -> None:
        target = next(iter(self.expected_contents))
        foreign = self.root / "foreign-link"
        os.link(target, foreign)
        with self.assertRaisesRegex(RuntimeError, "externally hardlinked"):
            self.compact()
        self.assertEqual(foreign.read_bytes(), self.expected_contents[target])

    def test_inventory_rejects_unknown_or_corrupt_pool_entry(self) -> None:
        self.compact()
        bad = self.pool_files() / "not-a-digest"
        bad.write_bytes(b"bad\n")
        bad.chmod(0o444)
        with self.assertRaisesRegex(RuntimeError, "entry name"):
            pack_cache.inventory(self.cache)

    def test_inventory_rejects_validly_named_symlink_entry(self) -> None:
        self.compact()
        entry = next(self.pool_files().iterdir())
        external = self.root / "external-entry"
        shutil.copyfile(entry, external)
        external.chmod(0o444)
        entry.unlink()
        entry.symlink_to(external)

        with self.assertRaisesRegex(RuntimeError, "cannot open pack-cache file"):
            pack_cache.inventory(self.cache)

    def test_failed_pool_copy_removes_temporary_file(self) -> None:
        source = self.root / "copy-source"
        source.write_bytes(b"copy payload\n")
        source.chmod(0o444)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = self.root / digest

        with mock.patch.object(pack_cache.os, "fsync", side_effect=OSError("synthetic fsync")):
            with self.assertRaisesRegex(RuntimeError, "cannot populate"):
                pack_cache.copy_entry(source, destination, digest)

        self.assertFalse(destination.exists())
        self.assertEqual(source.read_bytes(), b"copy payload\n")
        self.assertFalse(any(path.name.endswith(".new") for path in self.root.iterdir()))


if __name__ == "__main__":
    unittest.main()
