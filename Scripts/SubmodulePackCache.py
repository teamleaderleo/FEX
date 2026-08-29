#!/usr/bin/env python3
"""Content-addressed hardlink cache for immutable shallow-submodule pack files."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import stat
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


FORMAT = "teamleaderleo-fex-submodule-pack-cache-v1"
INVENTORY_FORMAT = "teamleaderleo-fex-submodule-pack-cache-inventory-v1"
POOL_DIRECTORY = "submodule-packs-v1"
ALLOWED_SUFFIXES = {".idx", ".pack", ".rev"}
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def git_output(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def require_directory(path: Path, *, create: bool = False, mode: int = 0o700) -> None:
    if create:
        try:
            path.mkdir(mode=mode, parents=False, exist_ok=True)
        except OSError as error:
            raise RuntimeError(f"cannot create pack-cache directory {path}: {error}") from error
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise RuntimeError(f"cannot inspect pack-cache directory {path}: {error}") from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"pack-cache path is not a real directory: {path}")
    if metadata.st_uid != os.getuid():
        raise RuntimeError(f"pack-cache directory has a foreign owner: {path}")


def pool_root(cache_root: Path) -> Path:
    return cache_root / POOL_DIRECTORY


def generation_root(cache_root: Path, generation: str) -> Path:
    if not DIGEST_PATTERN.fullmatch(generation):
        raise RuntimeError("pack-cache generation must be one SHA-256 digest")
    return pool_root(cache_root) / "generations" / generation


@contextmanager
def cache_lock(cache_root: Path, *, shared: bool, create: bool) -> Iterator[None]:
    locks = cache_root / "locks"
    if create:
        cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        require_directory(cache_root)
        locks.mkdir(mode=0o700, exist_ok=True)
    require_directory(cache_root)
    require_directory(locks)
    lock_path = locks / "submodule-packs.lock"
    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise RuntimeError(f"cannot open pack-cache lock: {error}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError("pack-cache lock is unsafe")
        operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        try:
            fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("submodule pack cache is active") from error
        yield
    finally:
        os.close(descriptor)


def module_root(source: Path) -> Path:
    gitdir = Path(git_output("-C", str(source), "rev-parse", "--absolute-git-dir"))
    root = gitdir / "modules"
    require_directory(root)
    return root


def module_repositories(root: Path, expected_count: int | None = None) -> list[tuple[str, Path]]:
    require_directory(root)
    result = []
    for config in root.rglob("config"):
        repository = config.parent
        if (repository / "objects").is_dir() and (repository / "HEAD").is_file():
            result.append((str(repository.relative_to(root)), repository))
    result.sort()
    if expected_count is not None and len(result) != expected_count:
        raise RuntimeError(
            f"expected {expected_count} recursive module Gitdirs, found {len(result)}"
        )
    return result


def pack_files(root: Path, expected_count: int | None = None) -> list[Path]:
    result = []
    for _, repository in module_repositories(root, expected_count):
        pack_directory = repository / "objects" / "pack"
        require_directory(pack_directory)
        with os.scandir(pack_directory) as entries:
            paths = sorted((Path(entry.path) for entry in entries), key=lambda path: path.name)
        for path in paths:
            metadata = os.lstat(path)
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise RuntimeError(f"unsafe pack-cache candidate: {path}")
            if path.suffix not in ALLOWED_SUFFIXES:
                raise RuntimeError(f"unsupported pack-cache candidate: {path}")
            if stat.S_IMODE(metadata.st_mode) != 0o444:
                raise RuntimeError(f"pack-cache candidate is not mode 0444: {path}")
            if metadata.st_uid != os.getuid():
                raise RuntimeError(f"pack-cache candidate has a foreign owner: {path}")
            result.append(path)
    if not result:
        raise RuntimeError("recursive module graph contains no pack-cache candidates")
    return result


def file_digest(path: Path) -> tuple[str, os.stat_result]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        raise RuntimeError(f"cannot open pack-cache file {path}: {error}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"pack-cache file is not regular: {path}")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise RuntimeError(f"pack-cache file changed while hashing: {path}")
    return digest.hexdigest(), after


def copy_entry(source: Path, destination: Path, digest: str) -> None:
    temporary = destination.with_name(f".{digest}.{os.getpid()}.new")
    source_descriptor = None
    target_descriptor = None
    try:
        source_descriptor = os.open(source, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        target_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o400,
        )
        copied_digest = hashlib.sha256()
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            copied_digest.update(chunk)
            view = memoryview(chunk)
            while view:
                view = view[os.write(target_descriptor, view) :]
        if copied_digest.hexdigest() != digest:
            raise RuntimeError("pack-cache source changed during copy")
        os.fchmod(target_descriptor, 0o444)
        os.fsync(target_descriptor)
        os.close(target_descriptor)
        target_descriptor = None
        os.close(source_descriptor)
        source_descriptor = None
        os.replace(temporary, destination)
    except OSError as error:
        raise RuntimeError(f"cannot populate pack-cache entry {destination}: {error}") from error
    finally:
        if source_descriptor is not None:
            os.close(source_descriptor)
        if target_descriptor is not None:
            os.close(target_descriptor)
        temporary.unlink(missing_ok=True)


def validate_pool_entry(path: Path, expected_digest: str) -> os.stat_result:
    if path.name != expected_digest or not DIGEST_PATTERN.fullmatch(path.name):
        raise RuntimeError(f"invalid pack-cache entry name: {path}")
    digest, metadata = file_digest(path)
    if digest != expected_digest:
        raise RuntimeError(f"pack-cache entry digest mismatch: {path}")
    if stat.S_IMODE(metadata.st_mode) != 0o444 or metadata.st_uid != os.getuid():
        raise RuntimeError(f"pack-cache entry has unsafe metadata: {path}")
    return metadata


def inode_map(root: Path) -> dict[tuple[int, int], int]:
    require_directory(root)
    root_metadata = os.lstat(root)
    root_device = root_metadata.st_dev
    result: dict[tuple[int, int], int] = {}
    pending = [root]
    while pending:
        path = pending.pop()
        metadata = os.lstat(path)
        if metadata.st_dev != root_device:
            raise RuntimeError(f"pack-cache tree crosses a filesystem boundary: {path}")
        result.setdefault((metadata.st_dev, metadata.st_ino), metadata.st_blocks * 512)
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            with os.scandir(path) as entries:
                pending.extend(Path(entry.path) for entry in entries)
    return result


def origin_digest(root: Path, expected_count: int) -> str:
    values = []
    for name, repository in module_repositories(root, expected_count):
        origin = git_output(
            f"--git-dir={repository}", "config", "--get", "remote.origin.url"
        )
        values.append(f"{name} {origin}")
    return hashlib.sha256(("\n".join(values) + "\n").encode("utf-8")).hexdigest()


def require_no_alternates(root: Path, expected_count: int) -> None:
    for _, repository in module_repositories(root, expected_count):
        path = repository / "objects" / "info" / "alternates"
        if path.exists() or path.is_symlink():
            raise RuntimeError(f"pack-cache consumer contains an alternate: {path}")


def compact(
    source: Path,
    cache_root: Path,
    generation: str,
    expected_repositories: int,
) -> dict[str, object]:
    started = time.monotonic()
    root = module_root(source)
    origins_before = origin_digest(root, expected_repositories)
    require_no_alternates(root, expected_repositories)
    targets = pack_files(root, expected_repositories)
    with cache_lock(cache_root, shared=False, create=True):
        pool = pool_root(cache_root)
        generations = pool / "generations"
        for path in (pool, generations):
            require_directory(path, create=True)
        generation_directory = generation_root(cache_root, generation)
        require_directory(generation_directory, create=True)
        files = generation_directory / "files"
        require_directory(files, create=True)
        if os.lstat(files).st_dev != os.lstat(root).st_dev:
            raise RuntimeError(
                "pack cache and submodule Gitdirs must share a filesystem; rerun without --pack-cache"
            )

        entries_before = len(list(files.iterdir()))
        copied = 0
        linked = 0
        already_linked = 0
        linked_reported_bytes = 0
        for index, target in enumerate(targets):
            digest, target_metadata = file_digest(target)
            entry = files / digest
            if entry.exists() or entry.is_symlink():
                entry_metadata = validate_pool_entry(entry, digest)
            else:
                copy_entry(target, entry, digest)
                entry_metadata = validate_pool_entry(entry, digest)
                copied += 1
            if (target_metadata.st_dev, target_metadata.st_ino) == (
                entry_metadata.st_dev,
                entry_metadata.st_ino,
            ):
                already_linked += 1
                linked_reported_bytes += entry_metadata.st_blocks * 512
                continue
            if target_metadata.st_nlink != 1:
                raise RuntimeError(f"refusing externally hardlinked pack-cache candidate: {target}")
            temporary = target.parent / f".pack-cache-link-{os.getpid()}-{index}"
            os.link(entry, temporary)
            current = os.lstat(target)
            if (
                current.st_dev != target_metadata.st_dev
                or current.st_ino != target_metadata.st_ino
                or current.st_size != target_metadata.st_size
                or current.st_mtime_ns != target_metadata.st_mtime_ns
                or current.st_mode != target_metadata.st_mode
            ):
                temporary.unlink()
                raise RuntimeError(f"pack-cache target changed before relink: {target}")
            os.replace(temporary, target)
            replacement = os.lstat(target)
            if (replacement.st_dev, replacement.st_ino) != (
                entry_metadata.st_dev,
                entry_metadata.st_ino,
            ):
                raise RuntimeError(f"pack-cache relink failed: {target}")
            linked += 1
            linked_reported_bytes += entry_metadata.st_blocks * 512

        pool_entries = sorted(files.iterdir(), key=lambda path: path.name)
        for entry in pool_entries:
            validate_pool_entry(entry, entry.name)
        pool_inodes = inode_map(files)
        consumer_inodes = inode_map(root)
        marginal = sum(value for identity, value in consumer_inodes.items() if identity not in pool_inodes)
        shared = set(pool_inodes) & set(consumer_inodes)
        pool_digest_payload = "\n".join(
            f"{entry.name} {entry.stat().st_size}" for entry in pool_entries
        ) + "\n"

    origins_after = origin_digest(root, expected_repositories)
    if origins_after != origins_before:
        raise RuntimeError("pack-cache compaction changed module origins")
    require_no_alternates(root, expected_repositories)
    return {
        "format": FORMAT,
        "generation": generation,
        "sourceFiles": len(targets),
        "entriesBefore": entries_before,
        "entriesAfter": len(pool_entries),
        "copiedEntries": copied,
        "linkedEntries": linked,
        "alreadyLinkedEntries": already_linked,
        "linkedReportedBytes": linked_reported_bytes,
        "sharedPoolInodes": len(shared),
        "sharedPoolAllocatedBytes": sum(pool_inodes[identity] for identity in shared),
        "consumerUniqueMarginalBytes": marginal,
        "consumerStoreReportedBytes": sum(consumer_inodes.values()),
        "poolAllocatedBytes": sum(pool_inodes.values()),
        "poolDigest": hashlib.sha256(pool_digest_payload.encode("utf-8")).hexdigest(),
        "originDigest": origins_after,
        "elapsedSeconds": round(time.monotonic() - started, 6),
    }


def empty_inventory(cache_root: Path) -> dict[str, object]:
    return {
        "format": INVENTORY_FORMAT,
        "cacheRoot": str(cache_root),
        "generations": [],
        "totals": {
            "generations": 0,
            "entries": 0,
            "allocatedBytes": 0,
            "linkedAllocatedBytes": 0,
            "reclaimableAllocatedBytes": 0,
            "consumerLinks": 0,
        },
    }


def inventory(cache_root: Path) -> dict[str, object]:
    pool = pool_root(cache_root)
    if not pool.exists() and not pool.is_symlink():
        return empty_inventory(cache_root)
    with cache_lock(cache_root, shared=True, create=False):
        require_directory(pool)
        generations_root = pool / "generations"
        require_directory(generations_root)
        records = []
        for generation_directory in sorted(generations_root.iterdir(), key=lambda path: path.name):
            require_directory(generation_directory)
            generation = generation_directory.name
            if not DIGEST_PATTERN.fullmatch(generation):
                raise RuntimeError(f"unsafe pack-cache generation: {generation}")
            files = generation_directory / "files"
            require_directory(files)
            linked_bytes = 0
            reclaimable_bytes = 0
            consumer_links = 0
            payload = []
            entries = sorted(files.iterdir(), key=lambda path: path.name)
            for entry in entries:
                metadata = validate_pool_entry(entry, entry.name)
                allocated = metadata.st_blocks * 512
                consumer_links += metadata.st_nlink - 1
                if metadata.st_nlink > 1:
                    linked_bytes += allocated
                else:
                    reclaimable_bytes += allocated
                payload.append(f"{entry.name} {metadata.st_size}")
            records.append(
                {
                    "generation": generation,
                    "entries": len(entries),
                    "allocatedBytes": sum(inode_map(files).values()),
                    "linkedAllocatedBytes": linked_bytes,
                    "reclaimableAllocatedBytes": reclaimable_bytes,
                    "consumerLinks": consumer_links,
                    "poolDigest": hashlib.sha256(
                        (("\n".join(payload) + "\n") if payload else "").encode("utf-8")
                    ).hexdigest(),
                }
            )
    return {
        "format": INVENTORY_FORMAT,
        "cacheRoot": str(cache_root),
        "generations": records,
        "totals": {
            "generations": len(records),
            "entries": sum(record["entries"] for record in records),
            "allocatedBytes": sum(record["allocatedBytes"] for record in records),
            "linkedAllocatedBytes": sum(record["linkedAllocatedBytes"] for record in records),
            "reclaimableAllocatedBytes": sum(
                record["reclaimableAllocatedBytes"] for record in records
            ),
            "consumerLinks": sum(record["consumerLinks"] for record in records),
        },
    }
