#!/usr/bin/env python3
"""Origin-bound shallow repositories for fast, self-contained submodule clones."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import IO, Iterator
from urllib.parse import urlsplit

import SubmodulePackCache as pack_cache


FORMAT = "teamleaderleo-fex-submodule-origin-cache-v1"
INVENTORY_FORMAT = "teamleaderleo-fex-submodule-origin-cache-inventory-v1"
MANIFEST_FORMAT = "teamleaderleo-fex-submodule-origin-manifest-v1"
ORIGIN_FORMAT = "teamleaderleo-fex-submodule-origin-v1"
CACHE_DIRECTORY = "submodule-origins-v1"
SHA1_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
SUBMODULE_KEY_PATTERN = re.compile(r"submodule\.(.+)\.(path|url)\Z")
MAX_MANIFEST_BYTES = 1024 * 1024


def git_run(*arguments: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *arguments], check=True, **kwargs)


def git_output(*arguments: str) -> str:
    return git_run(
        *arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def require_directory(path: Path, *, create: bool = False) -> None:
    if create:
        try:
            path.mkdir(mode=0o700, parents=False, exist_ok=True)
        except OSError as error:
            raise RuntimeError(f"cannot create origin-cache directory {path}: {error}") from error
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise RuntimeError(f"cannot inspect origin-cache directory {path}: {error}") from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"origin-cache path is not a real directory: {path}")
    if metadata.st_uid != os.getuid():
        raise RuntimeError(f"origin-cache directory has a foreign owner: {path}")


def validate_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in value
    ):
        raise RuntimeError(f"unsafe submodule path: {value!r}")
    return value


def validate_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() or ord(character) < 0x20 for character in value)
    ):
        raise RuntimeError(f"origin cache requires one absolute HTTPS URL: {value!r}")
    return value


def parse_gitmodules(source: Path) -> list[dict[str, str]]:
    completed = git_run(
        "-C",
        str(source),
        "config",
        "-z",
        "-f",
        ".gitmodules",
        "--get-regexp",
        r"^submodule\..*\.(path|url)$",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    fields: dict[str, dict[str, str]] = {}
    for entry in completed.stdout.split(b"\0"):
        if not entry:
            continue
        try:
            key_bytes, value_bytes = entry.split(b"\n", 1)
            key = key_bytes.decode("utf-8")
            value = value_bytes.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise RuntimeError("cannot parse .gitmodules as bounded UTF-8 Git config") from error
        match = SUBMODULE_KEY_PATTERN.fullmatch(key)
        if match is None:
            raise RuntimeError(f"unexpected .gitmodules key: {key}")
        name, field = match.groups()
        record = fields.setdefault(name, {})
        if field in record:
            raise RuntimeError(f"duplicate submodule {field}: {name}")
        record[field] = value

    rows = []
    for name, record in fields.items():
        if set(record) != {"path", "url"}:
            raise RuntimeError(f"submodule lacks one path/url pair: {name}")
        rows.append(
            {
                "name": name,
                "path": validate_path(record["path"]),
                "origin": validate_origin(record["url"]),
            }
        )
    rows.sort(key=lambda row: row["path"])
    paths = [row["path"] for row in rows]
    if not rows or len(paths) != len(set(paths)):
        raise RuntimeError(".gitmodules contains no submodules or duplicate paths")
    return rows


def index_gitlink(source: Path, path: str) -> str:
    output = git_run(
        "-C",
        str(source),
        "ls-files",
        "--stage",
        "-z",
        "--",
        path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    entries = [entry for entry in output.split(b"\0") if entry]
    if len(entries) != 1:
        raise RuntimeError(f"submodule path does not have one index entry: {path}")
    try:
        metadata, recorded_path = entries[0].decode("utf-8").split("\t", 1)
        mode, commit, stage = metadata.split(" ")
    except (UnicodeDecodeError, ValueError) as error:
        raise RuntimeError(f"cannot parse submodule index entry: {path}") from error
    if recorded_path != path or mode != "160000" or stage != "0" or not SHA1_PATTERN.fullmatch(commit):
        raise RuntimeError(f"unsafe submodule index entry: {path}")
    return commit


def top_level_identity(source: Path) -> dict[str, object]:
    rows = []
    for record in parse_gitmodules(source):
        rows.append(
            {
                "path": record["path"],
                "pin": index_gitlink(source, record["path"]),
                "origin": record["origin"],
            }
        )
    return {
        "rows": rows,
        "digest": top_level_digest(rows),
    }


def top_level_digest(rows: list[dict[str, str]]) -> str:
    payload = "".join(
        f"{row['pin']} {row['path']} {row['origin']}\n" for row in rows
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_top_rows(raw_rows: object) -> list[dict[str, str]]:
    if not isinstance(raw_rows, list):
        raise RuntimeError("origin-cache manifest lacks top-level rows")
    rows = []
    for raw in raw_rows:
        if not isinstance(raw, dict) or set(raw) != {"path", "pin", "origin"}:
            raise RuntimeError("origin-cache manifest has an invalid top-level row")
        if not isinstance(raw.get("path"), str) or not isinstance(raw.get("origin"), str):
            raise RuntimeError("origin-cache top-level path/origin is malformed")
        path = validate_path(raw["path"])
        pin = raw.get("pin")
        origin = validate_origin(raw["origin"])
        if not isinstance(pin, str) or not SHA1_PATTERN.fullmatch(pin):
            raise RuntimeError("origin-cache top-level pin is malformed")
        rows.append({"path": path, "pin": pin, "origin": origin})
    sorted_rows = sorted(rows, key=lambda row: row["path"])
    if rows != sorted_rows or len({row["path"] for row in rows}) != len(rows) or not rows:
        raise RuntimeError("origin-cache top-level rows are unordered, duplicated, or empty")
    return rows


def recursive_graph(source: Path) -> list[dict[str, object]]:
    status = git_run(
        "-C",
        str(source),
        "submodule",
        "status",
        "--recursive",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.rstrip("\n")
    module_root = pack_cache.module_root(source).resolve()
    rows = []
    for line in status.splitlines():
        if line[:1] != " " or len(line) < 43:
            raise RuntimeError("origin cache requires initialized exact recursive submodules")
        pin = line[1:41]
        path = validate_path(line[42:].split(" (", 1)[0])
        if not SHA1_PATTERN.fullmatch(pin):
            raise RuntimeError(f"cannot parse recursive submodule pin: {path}")
        worktree = source / path
        head = git_output("-C", str(worktree), "rev-parse", "HEAD")
        origin = validate_origin(
            git_output("-C", str(worktree), "config", "--get", "remote.origin.url")
        )
        gitdir = Path(
            git_output("-C", str(worktree), "rev-parse", "--absolute-git-dir")
        ).resolve()
        try:
            gitdir.relative_to(module_root)
        except ValueError as error:
            raise RuntimeError(f"submodule Gitdir is outside the superproject: {path}") from error
        alternate = gitdir / "objects" / "info" / "alternates"
        if alternate.exists() or alternate.is_symlink():
            raise RuntimeError(f"origin-cache consumer contains an alternate: {path}")
        if head != pin:
            raise RuntimeError(f"submodule HEAD differs from recorded pin: {path}")
        rows.append(
            {
                "path": path,
                "pin": pin,
                "origin": origin,
                "originId": hashlib.sha256(origin.encode("utf-8")).hexdigest(),
                "gitdir": gitdir,
            }
        )
    rows.sort(key=lambda row: row["path"])
    paths = [row["path"] for row in rows]
    if not rows or len(paths) != len(set(paths)):
        raise RuntimeError("recursive submodule graph is empty or has duplicate paths")
    return rows


def public_rows(rows: list[dict[str, object]]) -> list[dict[str, str]]:
    return [
        {
            "path": str(row["path"]),
            "pin": str(row["pin"]),
            "origin": str(row["origin"]),
            "originId": str(row["originId"]),
        }
        for row in rows
    ]


def pin_digest(rows: list[dict[str, object]]) -> str:
    payload = "\n".join(sorted(f"{row['pin']} {row['path']}" for row in rows)) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def path_origin_digest(rows: list[dict[str, object]]) -> str:
    payload = "\n".join(sorted(f"{row['path']} {row['origin']}" for row in rows)) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_root(cache: Path) -> Path:
    return cache / CACHE_DIRECTORY


def manifest_path(cache: Path, generation: str) -> Path:
    if not SHA256_PATTERN.fullmatch(generation):
        raise RuntimeError("origin-cache generation must be one SHA-256 digest")
    return cache_root(cache) / "generations" / generation / "manifest.json"


def origins_root(cache: Path) -> Path:
    return cache_root(cache) / "origins"


def origin_directory(cache: Path, origin_id: str) -> Path:
    if not SHA256_PATTERN.fullmatch(origin_id):
        raise RuntimeError("origin-cache origin ID must be one SHA-256 digest")
    return origins_root(cache) / origin_id


@contextmanager
def cache_lock(cache: Path, *, shared: bool, create: bool) -> Iterator[None]:
    locks = cache / "locks"
    if create:
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        require_directory(cache)
        locks.mkdir(mode=0o700, exist_ok=True)
    require_directory(cache)
    require_directory(locks)
    lock_path = locks / "submodule-origins.lock"
    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise RuntimeError(f"cannot open origin-cache lock: {error}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError("origin-cache lock is unsafe")
        operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        try:
            fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("submodule origin cache is active") from error
        yield
    finally:
        os.close(descriptor)


def atomic_json(path: Path, value: dict[str, object]) -> tuple[str, bytes]:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.new")
    descriptor = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o400,
        )
        view = memoryview(payload)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fchmod(descriptor, 0o444)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    return digest, payload


def read_json(path: Path) -> tuple[dict[str, object], str]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        raise RuntimeError(f"cannot open origin-cache metadata: {path}") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o444
            or metadata.st_size > MAX_MANIFEST_BYTES
        ):
            raise RuntimeError(f"origin-cache metadata is unsafe: {path}")
        payload = b""
        while len(payload) <= MAX_MANIFEST_BYTES:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            payload += chunk
    finally:
        os.close(descriptor)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"origin-cache metadata is invalid JSON: {path}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"origin-cache metadata is not an object: {path}")
    return value, hashlib.sha256(payload).hexdigest()


def validate_manifest(
    value: dict[str, object], generation: str, top_rows: list[dict[str, str]]
) -> list[dict[str, str]]:
    checked_top_rows = validate_top_rows(top_rows)
    if (
        value.get("format") != MANIFEST_FORMAT
        or value.get("generation") != generation
        or generation != top_level_digest(checked_top_rows)
        or value.get("topLevel") != checked_top_rows
    ):
        raise RuntimeError("origin-cache manifest identity mismatch")
    raw_rows = value.get("recursive")
    if not isinstance(raw_rows, list):
        raise RuntimeError("origin-cache manifest lacks recursive rows")
    rows = []
    for raw in raw_rows:
        if not isinstance(raw, dict) or set(raw) != {"path", "pin", "origin", "originId"}:
            raise RuntimeError("origin-cache manifest has an invalid recursive row")
        if not isinstance(raw.get("path"), str) or not isinstance(raw.get("origin"), str):
            raise RuntimeError("origin-cache recursive path/origin is malformed")
        path = validate_path(raw["path"])
        pin = raw.get("pin")
        origin = validate_origin(raw["origin"])
        origin_id = raw.get("originId")
        if (
            not isinstance(pin, str)
            or not SHA1_PATTERN.fullmatch(pin)
            or not isinstance(origin_id, str)
            or origin_id != hashlib.sha256(origin.encode()).hexdigest()
        ):
            raise RuntimeError("origin-cache manifest row identity mismatch")
        rows.append({"path": path, "pin": pin, "origin": origin, "originId": origin_id})
    rows.sort(key=lambda row: row["path"])
    if not rows or rows != raw_rows or len({row["path"] for row in rows}) != len(rows):
        raise RuntimeError("origin-cache recursive rows are unordered or duplicated")
    if value.get("recursivePinDigest") != pin_digest(rows):
        raise RuntimeError("origin-cache recursive pin digest mismatch")
    if value.get("pathOriginDigest") != path_origin_digest(rows):
        raise RuntimeError("origin-cache path/origin digest mismatch")
    if value.get("repositories") != len(rows) or value.get("origins") != len(
        {row["origin"] for row in rows}
    ):
        raise RuntimeError("origin-cache manifest counts mismatch")
    return rows


def validate_origin_repository(cache: Path, origin: str, origin_id: str) -> Path:
    directory = origin_directory(cache, origin_id)
    require_directory(directory)
    metadata, _ = read_json(directory / "origin.json")
    if metadata != {"format": ORIGIN_FORMAT, "origin": origin, "originId": origin_id}:
        raise RuntimeError("origin-cache repository metadata mismatch")
    repository = directory / "repo.git"
    require_directory(repository)
    if git_output(f"--git-dir={repository}", "rev-parse", "--is-bare-repository") != "true":
        raise RuntimeError("origin-cache repository is not bare")
    if git_output(f"--git-dir={repository}", "rev-parse", "--is-shallow-repository") != "true":
        raise RuntimeError("origin-cache repository is not shallow")
    return repository


def pin_available(repository: Path, pin: str) -> bool:
    return subprocess.run(
        ["git", f"--git-dir={repository}", "cat-file", "-e", f"{pin}^{{commit}}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def pin_ref_available(repository: Path, pin: str) -> bool:
    completed = subprocess.run(
        [
            "git",
            f"--git-dir={repository}",
            "show-ref",
            "--verify",
            "--hash",
            f"refs/fex-pins/{pin}",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return completed.returncode == 0 and completed.stdout.strip() == pin


def fetch_pin(repository: Path, row: dict[str, object]) -> None:
    source = Path(row["gitdir"])
    if git_output(f"--git-dir={source}", "rev-parse", "HEAD") != row["pin"]:
        raise RuntimeError(f"origin-cache source moved before import: {row['path']}")
    git_run(
        "-c",
        "protocol.file.allow=always",
        f"--git-dir={repository}",
        "fetch",
        "--quiet",
        "--depth",
        "1",
        source.as_uri(),
        f"HEAD:refs/fex-pins/{row['pin']}",
    )


def populate_origin(cache: Path, origin: str, rows: list[dict[str, object]]) -> tuple[int, int]:
    origin_id = hashlib.sha256(origin.encode("utf-8")).hexdigest()
    directory = origin_directory(cache, origin_id)
    fetched = 0
    reused = 0
    if directory.exists() or directory.is_symlink():
        repository = validate_origin_repository(cache, origin, origin_id)
        for row in rows:
            if pin_available(repository, str(row["pin"])):
                git_run(
                    f"--git-dir={repository}",
                    "update-ref",
                    f"refs/fex-pins/{row['pin']}",
                    str(row["pin"]),
                )
                reused += 1
                continue
            fetch_pin(repository, row)
            fetched += 1
    else:
        temporary = directory.with_name(f".{origin_id}.{os.getpid()}.new")
        if temporary.exists() or temporary.is_symlink():
            raise RuntimeError(f"origin-cache temporary path already exists: {temporary}")
        try:
            temporary.mkdir(mode=0o700)
            repository = temporary / "repo.git"
            git_run("init", "--bare", "--quiet", str(repository))
            for row in rows:
                if pin_available(repository, str(row["pin"])):
                    git_run(
                        f"--git-dir={repository}",
                        "update-ref",
                        f"refs/fex-pins/{row['pin']}",
                        str(row["pin"]),
                    )
                    reused += 1
                else:
                    fetch_pin(repository, row)
                    fetched += 1
            git_run(
                f"--git-dir={repository}",
                "update-ref",
                "refs/heads/cache",
                str(rows[0]["pin"]),
            )
            git_run(
                f"--git-dir={repository}",
                "symbolic-ref",
                "HEAD",
                "refs/heads/cache",
            )
            atomic_json(
                temporary / "origin.json",
                {"format": ORIGIN_FORMAT, "origin": origin, "originId": origin_id},
            )
            os.replace(temporary, directory)
            parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        repository = validate_origin_repository(cache, origin, origin_id)
    for row in rows:
        if not pin_available(repository, str(row["pin"])) or not pin_ref_available(
            repository, str(row["pin"])
        ):
            raise RuntimeError(f"origin-cache pin import failed: {row['path']}")
    git_run(
        f"--git-dir={repository}",
        "fsck",
        "--full",
        "--no-reflogs",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return fetched, reused


def make_manifest(
    generation: str,
    top_rows: list[dict[str, str]],
    graph: list[dict[str, object]],
) -> dict[str, object]:
    rows = public_rows(graph)
    return {
        "format": MANIFEST_FORMAT,
        "generation": generation,
        "topLevel": top_rows,
        "recursive": rows,
        "repositories": len(rows),
        "origins": len({row["origin"] for row in rows}),
        "recursivePinDigest": pin_digest(rows),
        "pathOriginDigest": path_origin_digest(rows),
    }


def populate(
    cache: Path,
    generation: str,
    top_rows: list[dict[str, str]],
    graph: list[dict[str, object]],
) -> tuple[dict[str, object], str, int, int]:
    root = cache_root(cache)
    generations = root / "generations"
    origins = root / "origins"
    for path in (root, generations, origins):
        require_directory(path, create=True)
    by_origin: dict[str, list[dict[str, object]]] = {}
    for row in graph:
        by_origin.setdefault(str(row["origin"]), []).append(row)
    fetched = 0
    reused = 0
    for origin, rows in sorted(by_origin.items()):
        imported, existing = populate_origin(cache, origin, rows)
        fetched += imported
        reused += existing
    generation_directory = generations / generation
    require_directory(generation_directory, create=True)
    path = generation_directory / "manifest.json"
    expected = make_manifest(generation, top_rows, graph)
    if path.exists() or path.is_symlink():
        current, digest = read_json(path)
        validate_manifest(current, generation, top_rows)
        if current != expected:
            raise RuntimeError("origin-cache generation manifest changed")
    else:
        digest, _ = atomic_json(path, expected)
    return expected, digest, fetched, reused


def local_update_command(
    source: Path,
    jobs: int,
    repositories: dict[str, Path],
) -> list[str]:
    command = ["git", "-c", "protocol.file.allow=always"]
    for origin, repository in sorted(repositories.items()):
        command.extend(["-c", f"url.{repository.resolve().as_uri()}.insteadOf={origin}"])
    command.extend(
        [
            "-C",
            str(source),
            "submodule",
            "update",
            "--init",
            "--recursive",
            "--depth",
            "1",
            "--jobs",
            str(jobs),
        ]
    )
    return command


def ordinary_update_command(source: Path, jobs: int) -> list[str]:
    return [
        "git",
        "-C",
        str(source),
        "submodule",
        "update",
        "--init",
        "--recursive",
        "--depth",
        "1",
        "--jobs",
        str(jobs),
    ]


def compare_graph(actual: list[dict[str, object]], expected: list[dict[str, str]]) -> None:
    if public_rows(actual) != expected:
        raise RuntimeError("origin-cache materialization differs from its exact recursive manifest")


def update(
    source: Path,
    cache: Path,
    jobs: int,
    *,
    progress: IO[str] | None = None,
) -> dict[str, object]:
    started = time.monotonic()
    if cache.exists() or cache.is_symlink():
        require_directory(cache)
    top = top_level_identity(source)
    generation = str(top["digest"])
    top_rows = top["rows"]
    path = manifest_path(cache, generation)
    manifest_exists = path.exists() or path.is_symlink()
    update_started = time.monotonic()
    fetched = 0
    reused = 0
    if manifest_exists:
        with cache_lock(cache, shared=True, create=False):
            manifest, manifest_digest = read_json(path)
            expected = validate_manifest(manifest, generation, top_rows)
            repositories = {}
            for row in expected:
                repository = validate_origin_repository(
                    cache, row["origin"], row["originId"]
                )
                if not pin_available(repository, row["pin"]):
                    raise RuntimeError(f"origin-cache repository lacks exact pin: {row['path']}")
                if not pin_ref_available(repository, row["pin"]):
                    raise RuntimeError(
                        f"origin-cache repository does not advertise exact pin: {row['path']}"
                    )
                repositories[row["origin"]] = repository
            subprocess.run(
                local_update_command(source, jobs, repositories),
                check=True,
                stdout=progress,
            )
            graph = recursive_graph(source)
            compare_graph(graph, expected)
            allocated = sum(pack_cache.inode_map(cache_root(cache)).values())
        state = "warm_hit"
        local_origins = len(repositories)
    else:
        subprocess.run(ordinary_update_command(source, jobs), check=True, stdout=progress)
        graph = recursive_graph(source)
        top_graph = [
            {"path": row["path"], "pin": row["pin"], "origin": row["origin"]}
            for row in graph
            if row["path"] in {top_row["path"] for top_row in top_rows}
        ]
        if top_graph != top_rows:
            raise RuntimeError("cold origin-cache bootstrap differs from top-level identity")
        with cache_lock(cache, shared=False, create=True):
            manifest, manifest_digest, fetched, reused = populate(
                cache, generation, top_rows, graph
            )
            validate_manifest(manifest, generation, top_rows)
            allocated = sum(pack_cache.inode_map(cache_root(cache)).values())
        state = "cold_populated"
        local_origins = 0
    update_elapsed = time.monotonic() - update_started
    return {
        "format": FORMAT,
        "state": state,
        "generation": generation,
        "repositories": len(graph),
        "origins": len({str(row["origin"]) for row in graph}),
        "localOrigins": local_origins,
        "recursivePinDigest": pin_digest(graph),
        "pathOriginDigest": path_origin_digest(graph),
        "manifestDigest": manifest_digest,
        "fetchedPins": fetched,
        "reusedPins": reused,
        "allocatedBytes": allocated,
        "updateElapsedSeconds": round(update_elapsed, 6),
        "elapsedSeconds": round(time.monotonic() - started, 6),
        "persistentGitConfig": False,
        "alternates": 0,
    }


def empty_inventory(cache: Path) -> dict[str, object]:
    return {
        "format": INVENTORY_FORMAT,
        "cacheRoot": str(cache),
        "generations": [],
        "totals": {
            "generations": 0,
            "origins": 0,
            "advertisedPins": 0,
            "allocatedBytes": 0,
        },
    }


def inventory(cache: Path) -> dict[str, object]:
    root = cache_root(cache)
    if not root.exists() and not root.is_symlink():
        return empty_inventory(cache)
    with cache_lock(cache, shared=True, create=False):
        require_directory(root)
        generations = root / "generations"
        origins = root / "origins"
        require_directory(generations)
        require_directory(origins)
        generation_rows = []
        required_pins: set[tuple[str, str]] = set()
        for directory in sorted(generations.iterdir(), key=lambda path: path.name):
            require_directory(directory)
            generation = directory.name
            if not SHA256_PATTERN.fullmatch(generation):
                raise RuntimeError(f"unsafe origin-cache generation: {generation}")
            manifest, digest = read_json(directory / "manifest.json")
            top_rows = validate_top_rows(manifest.get("topLevel"))
            rows = validate_manifest(manifest, generation, top_rows)
            required_pins.update((row["originId"], row["pin"]) for row in rows)
            generation_rows.append(
                {
                    "generation": generation,
                    "repositories": len(rows),
                    "origins": len({row["origin"] for row in rows}),
                    "recursivePinDigest": manifest["recursivePinDigest"],
                    "pathOriginDigest": manifest["pathOriginDigest"],
                    "manifestDigest": digest,
                }
            )
        origin_count = 0
        advertised_pins = 0
        available_pins: set[tuple[str, str]] = set()
        for directory in sorted(origins.iterdir(), key=lambda path: path.name):
            require_directory(directory)
            origin_id = directory.name
            if not SHA256_PATTERN.fullmatch(origin_id):
                raise RuntimeError(f"unsafe origin-cache origin ID: {origin_id}")
            metadata, _ = read_json(directory / "origin.json")
            origin = metadata.get("origin")
            if not isinstance(origin, str):
                raise RuntimeError("origin-cache repository metadata lacks origin")
            repository = validate_origin_repository(cache, origin, origin_id)
            refs = git_output(
                f"--git-dir={repository}",
                "for-each-ref",
                "--format=%(refname) %(objectname)",
                "refs/fex-pins/",
            ).splitlines()
            for ref in refs:
                try:
                    name, pin = ref.split(" ", 1)
                except ValueError as error:
                    raise RuntimeError("cannot parse origin-cache pin ref") from error
                if name != f"refs/fex-pins/{pin}" or not SHA1_PATTERN.fullmatch(pin):
                    raise RuntimeError("origin-cache pin ref is malformed")
                if not pin_available(repository, pin):
                    raise RuntimeError("origin-cache advertised pin is unavailable")
                available_pins.add((origin_id, pin))
            git_run(
                f"--git-dir={repository}",
                "fsck",
                "--full",
                "--no-reflogs",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            origin_count += 1
            advertised_pins += len(refs)
        if required_pins - available_pins:
            raise RuntimeError(
                "origin-cache generation references unavailable advertised pins"
            )
        allocated = sum(pack_cache.inode_map(root).values())
    return {
        "format": INVENTORY_FORMAT,
        "cacheRoot": str(cache),
        "generations": generation_rows,
        "totals": {
            "generations": len(generation_rows),
            "origins": origin_count,
            "advertisedPins": advertised_pins,
            "allocatedBytes": allocated,
        },
    }
