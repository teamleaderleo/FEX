#!/usr/bin/env python3
"""Fast, explicit x86-host build lanes for this owned FEX research fork."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LANE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
LINUX_TEST_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
PROFILE = "x86-host-dev-v1"
CCACHE_SLOPPINESS = "time_macros"
DEFAULT_SUBMODULE_JOBS = min(os.cpu_count() or 1, 16)
CONFIGURE_OPTIONS = [
    "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
    "-DENABLE_LTO=False",
    "-DENABLE_ASSERTIONS=True",
    "-DENABLE_X86_HOST_DEBUG=True",
    "-DBUILD_THUNKS=True",
    "-DBUILD_TESTING=True",
    "-DBUILD_FEXCONFIG=False",
    "-DUSE_LINKER=lld",
]
CONFIGURE_PROFILES = {
    "dev": {
        "id": PROFILE,
        "options": CONFIGURE_OPTIONS,
    },
    "linux-tests": {
        "id": "x86-host-linux-tests-v1",
        "options": [*CONFIGURE_OPTIONS, "-DBUILD_FEX_LINUX_TESTS=True"],
    },
}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Initialize sources, configure, or build one exact FEX target."
    )
    result.add_argument("--lane", default="dev", help="isolated stable-path lane name")
    result.add_argument("--source", type=Path, default=REPO_ROOT, help="FEX worktree to expose")
    result.add_argument(
        "--profile",
        choices=CONFIGURE_PROFILES,
        default="dev",
        help="bounded CMake profile; linux-tests builds the guest Linux test binaries",
    )
    result.add_argument(
        "--cache-root",
        type=Path,
        default=default_cache_root(),
        help="external build/cache root",
    )
    subparsers = result.add_subparsers(dest="action", required=True)
    submodules = subparsers.add_parser(
        "submodules",
        help="explicitly initialize and verify pinned recursive submodules",
    )
    submodules.add_argument(
        "--jobs",
        type=positive_int,
        default=DEFAULT_SUBMODULE_JOBS,
        help="parallel submodule clone/fetch workers",
    )
    subparsers.add_parser(
        "lanes",
        help="inventory stable build lanes without mutating them",
    )
    subparsers.add_parser("configure", help="freshly configure the lane")
    subparsers.add_parser(
        "editor",
        help="configure as needed and write a worktree-local compile_commands.json",
    )
    build = subparsers.add_parser("build", help="build one named CMake target")
    build.add_argument("target", help="exact target, for example vulkan-host-64")
    build.add_argument(
        "--jobs", type=positive_int, default=min(os.cpu_count() or 1, 16), help="build workers"
    )
    linux_test = subparsers.add_parser(
        "linux-test-build",
        help="build FEX prerequisites plus one exact guest Linux test binary",
    )
    linux_test.add_argument("test", help="exact test basename, for example smc-2")
    linux_test.add_argument("--bitness", type=int, choices=(32, 64), default=64)
    linux_test.add_argument(
        "--jobs", type=positive_int, default=min(os.cpu_count() or 1, 16), help="build workers"
    )
    subparsers.add_parser("status", help="show the lane and its last build receipt")
    return result


def default_cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    return Path(base) / "fex-dev" if base else Path.home() / ".cache" / "fex-dev"


def positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def validate_lane(raw: str) -> str:
    if not LANE_PATTERN.fullmatch(raw):
        raise ValueError("lane must use letters, numbers, dot, underscore, or hyphen")
    return raw


def validate_linux_test(raw: str) -> str:
    if not LINUX_TEST_PATTERN.fullmatch(raw):
        raise ValueError("Linux test must be one exact target basename")
    return raw


def required_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"required tool is missing: {name}")
    return path


def cpu_namespace(profile: str = PROFILE) -> str:
    model = platform.machine()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name"):
                model += ":" + line.partition(":")[2].strip()
                break
    digest = hashlib.sha256(model.encode("utf-8")).hexdigest()[:12]
    return f"teamleaderleo-FEX:{profile}:{digest}"


def environment(cache_root: Path, lane_root: Path, profile: str = PROFILE) -> dict[str, str]:
    result = os.environ.copy()
    result.update(
        {
            "CC": required_tool("clang"),
            "CXX": required_tool("clang++"),
            "CCACHE_DIR": str(cache_root / "ccache"),
            "CCACHE_BASEDIR": str(lane_root),
            "CCACHE_NOHASHDIR": "true",
            "CCACHE_NAMESPACE": cpu_namespace(profile),
            "CCACHE_SLOPPINESS": CCACHE_SLOPPINESS,
        }
    )
    return result


def configure_command(
    source_view: Path,
    build: Path,
    configure_options: list[str] = CONFIGURE_OPTIONS,
) -> list[str]:
    return [
        required_tool("cmake"),
        "--fresh",
        "-S",
        str(source_view),
        "-B",
        str(build),
        "-G",
        "Ninja",
        *configure_options,
    ]


def reconfigure_command(
    source_view: Path,
    build: Path,
    configure_options: list[str] = CONFIGURE_OPTIONS,
) -> list[str]:
    """Refresh CMake's graph without throwing away a warm build tree."""
    return [
        required_tool("cmake"),
        "-S",
        str(source_view),
        "-B",
        str(build),
        "-G",
        "Ninja",
        *configure_options,
    ]


def build_command(build: Path, target: str, jobs: int) -> list[str]:
    if not target or target.startswith("-"):
        raise ValueError("target must be an explicit CMake target name")
    return [
        required_tool("cmake"),
        "--build",
        str(build),
        "--target",
        target,
        "--parallel",
        str(jobs),
    ]


def focused_linux_test_build(build: Path, bitness: int) -> Path:
    return build / "unittests" / "FEXLinuxTests" / f"FEXLinuxTests_{bitness}"


def configure_linux_test_command(
    source_view: Path, build: Path, bitness: int
) -> list[str]:
    return [
        required_tool("cmake"),
        "-S",
        str(source_view / "unittests" / "FEXLinuxTests" / "tests"),
        "-B",
        str(build),
        "-G",
        "Ninja",
        "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
        f"-DCMAKE_TOOLCHAIN_FILE:FILEPATH={source_view / 'Data' / 'CMake' / f'toolchain_x86_{bitness}.cmake'}",
        "-DENABLE_CLANG_THUNKS=True",
        f"-DBITNESS={bitness}",
    ]


def git_output(source: Path, *arguments: str) -> str:
    return subprocess.run(
        [required_tool("git"), "-C", str(source), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def submodule_update_command(source: Path, jobs: int) -> list[str]:
    return [
        required_tool("git"),
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


def source_identity(source: Path) -> dict[str, object]:
    return {
        "head": git_output(source, "rev-parse", "HEAD"),
        "dirty": bool(git_output(source, "status", "--porcelain")),
    }


def require_pinned_submodules(source: Path) -> None:
    completed = subprocess.run(
        [required_tool("git"), "-C", str(source), "submodule", "status", "--recursive"],
        check=True,
        capture_output=True,
        text=True,
    )
    invalid = [
        line
        for line in completed.stdout.splitlines()
        if line[:1] in {"-", "+", "U"}
    ]
    if not invalid:
        return

    paths = [line[1:].strip().split(maxsplit=1)[1].split(" ", 1)[0] for line in invalid]
    command = (
        f"git -C {source} submodule update --init --recursive --depth 1 "
        f"--jobs {DEFAULT_SUBMODULE_JOBS}"
    )
    raise RuntimeError(f"submodules are uninitialized or not pinned: {', '.join(paths)}; run: {command}")


def pinned_submodule_identity(source: Path) -> tuple[int, str]:
    completed = subprocess.run(
        [required_tool("git"), "-C", str(source), "submodule", "status", "--recursive"],
        check=True,
        capture_output=True,
        text=True,
    )
    normalized = []
    for line in completed.stdout.splitlines():
        if line[:1] in {"-", "+", "U"}:
            raise RuntimeError("cannot identify uninitialized or unpinned submodules")
        if len(line) < 43:
            raise RuntimeError("cannot parse recursive submodule status")
        commit = line[1:41]
        path = line[42:].split(" (", 1)[0]
        if not re.fullmatch(r"[0-9a-f]{40}", commit) or not path:
            raise RuntimeError("cannot parse recursive submodule status")
        normalized.append(f"{commit} {path}")
    if not normalized:
        raise RuntimeError("recursive submodule status is empty")
    payload = "\n".join(sorted(normalized)) + "\n"
    return len(normalized), hashlib.sha256(payload.encode("utf-8")).hexdigest()


def atomic_source_view(source_view: Path, source: Path) -> None:
    temporary = source_view.with_name(f".{source_view.name}.{os.getpid()}.new")
    os.symlink(source, temporary, target_is_directory=True)
    os.replace(temporary, source_view)


def prepare_source_view(
    source: Path,
    source_view: Path,
    build: Path,
    env: dict[str, str],
    extra_builds: tuple[Path, ...] = (),
    runner=subprocess.run,
) -> bool:
    """Expose source at one stable path; clean before switching an existing lane."""
    if source_view.exists() and not source_view.is_symlink():
        raise RuntimeError(f"refusing non-symlink source view: {source_view}")
    current = source_view.resolve() if source_view.is_symlink() else None
    if current == source:
        return False
    if current is not None:
        for old_build in (*extra_builds, build):
            if (old_build / "build.ninja").is_file():
                runner(
                    [
                        required_tool("cmake"),
                        "--build",
                        str(old_build),
                        "--target",
                        "clean",
                    ],
                    check=True,
                    env=env,
                )
    atomic_source_view(source_view, source)
    return True


def write_receipt(destination: Path, receipt: object) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.new")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


def replace_path(value: object, old: str, new: str) -> object:
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [replace_path(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: replace_path(item, old, new) for key, item in value.items()}
    return value


def write_editor_compile_commands(
    source_view: Path, source: Path, build: Path, destination: Path
) -> int:
    """Translate the stable-view compilation database to the editor's real worktree."""
    build_database = build / "compile_commands.json"
    try:
        entries = json.loads(build_database.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read compilation database: {build_database}") from error
    if not isinstance(entries, list) or not entries or not all(isinstance(item, dict) for item in entries):
        raise RuntimeError(f"invalid or empty compilation database: {build_database}")
    translated = replace_path(entries, str(source_view), str(source))
    write_receipt(destination, translated)
    return len(entries)


def expected_profile(
    cache_namespace: str,
    profile: str = PROFILE,
    configure_options: list[str] = CONFIGURE_OPTIONS,
) -> dict[str, object]:
    return {
        "format": "teamleaderleo-fex-x86-host-dev-profile-v1",
        "profile": profile,
        "configureOptions": configure_options,
        "cacheNamespace": cache_namespace,
        "ccacheSloppiness": CCACHE_SLOPPINESS,
    }


def profile_matches(path: Path, expected: dict[str, object]) -> bool:
    try:
        return json.loads(path.read_text(encoding="utf-8")) == expected
    except (OSError, json.JSONDecodeError):
        return False


def read_small_json(path: Path, limit: int = 1024 * 1024) -> tuple[str, object | None]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unsafe", None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            return "unsafe", None
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(payload) > limit:
        return "unsafe", None
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "invalid", None
    if not isinstance(value, dict):
        return "invalid", None
    return "valid", value


def allocated_bytes_no_follow(root: Path) -> int:
    """Match du-style allocated bytes without following links or crossing filesystems."""
    root_metadata = os.lstat(root)
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise RuntimeError(f"lane root is not a directory: {root}")
    root_device = root_metadata.st_dev
    pending = [root]
    seen: set[tuple[int, int]] = set()
    total = 0
    while pending:
        path = pending.pop()
        metadata = os.lstat(path)
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        total += metadata.st_blocks * 512
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            if metadata.st_dev != root_device:
                raise RuntimeError(f"lane crosses a filesystem boundary: {path}")
            with os.scandir(path) as entries:
                pending.extend(Path(entry.path) for entry in entries)
    return total


def source_view_state(source_view: Path) -> tuple[str, str | None]:
    try:
        metadata = os.lstat(source_view)
    except FileNotFoundError:
        return "missing", None
    if not stat.S_ISLNK(metadata.st_mode):
        return "unsafe", None
    target = Path(os.path.realpath(source_view))
    return ("live" if target.is_dir() else "dead"), str(target)


def lane_lock_state(lock_path: Path) -> str:
    try:
        descriptor = os.open(lock_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unsafe"
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return "unsafe"
        try:
            # Concurrent read-only inventories may share this probe. The helper's
            # actual lane owner holds an exclusive lock, which still blocks it.
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return "active"
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return "inactive"
    finally:
        os.close(descriptor)


def lane_build_state(build: Path) -> str:
    try:
        metadata = os.lstat(build)
    except FileNotFoundError:
        return "missing"
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        return "unsafe"
    try:
        marker = os.lstat(build / "build.ninja")
    except FileNotFoundError:
        return "unconfigured"
    return "configured" if stat.S_ISREG(marker.st_mode) else "unsafe"


def lane_inventory(cache_root: Path) -> dict[str, object]:
    views = cache_root / "views"
    locks = cache_root / "locks"
    records = []
    try:
        views_metadata = os.lstat(views)
    except FileNotFoundError:
        views_metadata = None
    if views_metadata is not None:
        if not stat.S_ISDIR(views_metadata.st_mode) or stat.S_ISLNK(views_metadata.st_mode):
            raise RuntimeError(f"views root is not a directory: {views}")
        try:
            locks_metadata = os.lstat(locks)
        except FileNotFoundError:
            locks_safe = True
        else:
            locks_safe = stat.S_ISDIR(locks_metadata.st_mode) and not stat.S_ISLNK(locks_metadata.st_mode)
        for lane_root in sorted(views.iterdir(), key=lambda path: path.name):
            lane = lane_root.name
            lane_state = "valid" if LANE_PATTERN.fullmatch(lane) else "unsafe"
            try:
                metadata = os.lstat(lane_root)
                if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                    lane_state = "unsafe"
            except OSError:
                lane_state = "unsafe"
            if lane_state == "valid":
                source_state, source_target = source_view_state(lane_root / "src")
                lock_state = lane_lock_state(locks / f"{lane}.lock") if locks_safe else "unsafe"
                receipt_state, receipt = read_small_json(lane_root / "last-receipt.json")
                profile_state, _ = read_small_json(lane_root / "profile.json")
                build_state = lane_build_state(lane_root / "build")
                try:
                    allocated_bytes = allocated_bytes_no_follow(lane_root)
                except (OSError, RuntimeError):
                    allocated_bytes = None
                    lane_state = "unsafe"
                if build_state == "unsafe" or lock_state == "unsafe":
                    lane_state = "unsafe"
            else:
                source_state, source_target = "unsafe", None
                lock_state = "unsafe"
                receipt_state, receipt = "unsafe", None
                profile_state = "unsafe"
                allocated_bytes = None
                build_state = "unsafe"
            receipt_summary = None
            if isinstance(receipt, dict):
                receipt_summary = {
                    key: receipt.get(key)
                    for key in ("format", "head", "dirty", "target", "test", "exitCode")
                }
            records.append(
                {
                    "lane": lane,
                    "laneState": lane_state,
                    "sourceState": source_state,
                    "sourceTarget": source_target,
                    "lockState": lock_state,
                    "allocatedBytes": allocated_bytes,
                    "buildState": build_state,
                    "receiptState": receipt_state,
                    "receipt": receipt_summary,
                    "profileState": profile_state,
                    "reviewCandidate": (
                        lane_state == "valid"
                        and source_state == "dead"
                        and lock_state in {"inactive", "missing"}
                        and receipt_state == "valid"
                    ),
                }
            )
    totals = {
        "lanes": len(records),
        "allocatedBytes": sum(
            record["allocatedBytes"]
            for record in records
            if isinstance(record["allocatedBytes"], int)
        ),
        "bySourceState": {
            state: sum(record["sourceState"] == state for record in records)
            for state in ("live", "dead", "missing", "unsafe")
        },
        "reviewCandidates": sum(record["reviewCandidate"] for record in records),
    }
    return {
        "format": "teamleaderleo-fex-dev-lane-inventory-v1",
        "cacheRoot": str(cache_root),
        "lanes": records,
        "totals": totals,
    }


def configuration_mode(
    action: str,
    switched: bool,
    build_configured: bool,
    profile_compatible: bool,
) -> str:
    """Choose fresh, incremental, or reused configuration without weakening profile checks."""
    if action == "configure" or not build_configured or not profile_compatible:
        return "fresh"
    if switched:
        return "incremental"
    return "reuse"


def locked_lane(cache_root: Path, lane: str):
    locks = cache_root / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    handle = (locks / f"{lane}.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        handle.close()
        raise RuntimeError(f"lane is already active: {lane}") from error
    return handle


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        lane = validate_lane(args.lane)
        selected_profile = CONFIGURE_PROFILES[args.profile]
        profile_id = str(selected_profile["id"])
        configure_options = list(selected_profile["options"])
        cache_root = args.cache_root.expanduser().resolve()
        if args.action == "lanes":
            print(json.dumps(lane_inventory(cache_root), indent=2, sort_keys=True))
            return 0
        source = args.source.resolve(strict=True)
        lane_root = cache_root / "views" / lane
        source_view = lane_root / "src"
        build = lane_root / "build"
        receipt_path = lane_root / "last-receipt.json"
        profile_path = lane_root / "profile.json"

        if args.action == "status":
            print(f"lane={lane}")
            print(f"source={source_view.resolve() if source_view.is_symlink() else 'unconfigured'}")
            print(f"build={build}")
            if receipt_path.is_file():
                print(receipt_path.read_text(encoding="utf-8"), end="")
            return 0

        if args.action == "submodules":
            started = time.monotonic()
            subprocess.run(submodule_update_command(source, args.jobs), check=True)
            require_pinned_submodules(source)
            repositories, digest = pinned_submodule_identity(source)
            identity = source_identity(source)
            receipt = {
                "format": "teamleaderleo-fex-submodule-bootstrap-receipt-v1",
                "head": identity["head"],
                "dirty": identity["dirty"],
                "jobs": args.jobs,
                "repositories": repositories,
                "pinnedDigest": digest,
                "elapsedSeconds": round(time.monotonic() - started, 6),
            }
            print(json.dumps(receipt, sort_keys=True))
            return 0

        require_pinned_submodules(source)
        for tool in ("ninja", "ccache", "ld.lld", "nasm", "pkg-config"):
            required_tool(tool)
        lane_root.mkdir(parents=True, exist_ok=True)
        build.mkdir(parents=True, exist_ok=True)
        env = environment(cache_root, lane_root, profile_id)
        with locked_lane(cache_root, lane):
            setup_started = time.monotonic()
            switched = prepare_source_view(
                source,
                source_view,
                build,
                env,
                (
                    focused_linux_test_build(build, 32),
                    focused_linux_test_build(build, 64),
                ),
            )
            profile_marker = expected_profile(
                env["CCACHE_NAMESPACE"], profile_id, configure_options
            )
            configure_mode = configuration_mode(
                args.action,
                switched,
                (build / "build.ninja").is_file(),
                profile_matches(profile_path, profile_marker),
            )
            if configure_mode == "fresh":
                subprocess.run(
                    configure_command(source_view, build, configure_options),
                    check=True,
                    env=env,
                )
                write_receipt(profile_path, profile_marker)
            elif configure_mode == "incremental":
                subprocess.run(
                    reconfigure_command(source_view, build, configure_options),
                    check=True,
                    env=env,
                )
            if args.action == "configure":
                print(f"configured lane={lane} source={source} build={build}")
                return 0
            if args.action == "editor":
                if configure_mode == "reuse":
                    subprocess.run(
                        reconfigure_command(source_view, build, configure_options),
                        check=True,
                        env=env,
                    )
                    configure_mode = "incremental"
                destination = source / "compile_commands.json"
                count = write_editor_compile_commands(
                    source_view, source, build, destination
                )
                print(
                    f"editor lane={lane} entries={count} "
                    f"configuration={configure_mode} "
                    f"compile_commands={destination} build={build}"
                )
                return 0

            setup_elapsed = time.monotonic() - setup_started
            identity = source_identity(source)
            if args.action == "linux-test-build":
                if args.profile != "linux-tests":
                    raise ValueError("linux-test-build action requires --profile linux-tests")
                test = validate_linux_test(args.test)
                guest_build = focused_linux_test_build(build, args.bitness)
                guest_target = f"{test}.{args.bitness}"
                commands = [
                    build_command(build, "FEX", args.jobs),
                    build_command(build, "FEXServer", args.jobs),
                    configure_linux_test_command(source_view, guest_build, args.bitness),
                    build_command(guest_build, guest_target, args.jobs),
                ]
                print(
                    f"scope=FOCUSED_LINUX_TEST profile={args.profile} lane={lane} "
                    f"test={test} bitness={args.bitness} head={identity['head']} "
                    f"dirty={str(identity['dirty']).lower()}"
                )
                print("runtime execution and other Linux tests are not implied")
                sys.stdout.flush()
                started = time.monotonic()
                completed = subprocess.CompletedProcess(commands[0], 0)
                for command in commands:
                    completed = subprocess.run(command, env=env)
                    if completed.returncode != 0:
                        break
                receipt = {
                    "format": "teamleaderleo-fex-x86-host-linux-test-build-receipt-v1",
                    "profile": profile_id,
                    "requestedProfile": args.profile,
                    "lane": lane,
                    "test": test,
                    "bitness": args.bitness,
                    "head": identity["head"],
                    "dirty": identity["dirty"],
                    "sourceSwitched": switched,
                    "configurationMode": configure_mode,
                    "setupElapsedSeconds": round(setup_elapsed, 6),
                    "jobs": args.jobs,
                    "elapsedSeconds": round(time.monotonic() - started, 6),
                    "exitCode": completed.returncode,
                    "cacheNamespace": env["CCACHE_NAMESPACE"],
                    "ccacheSloppiness": env["CCACHE_SLOPPINESS"],
                }
                write_receipt(receipt_path, receipt)
                print(f"guestBinary={guest_build / guest_target}")
                print(f"fexBinary={build / 'Bin' / 'FEX'}")
                print(json.dumps(receipt, sort_keys=True))
                return completed.returncode

            command = build_command(build, args.target, args.jobs)
            print(
                f"scope=FOCUSED_TARGET profile={args.profile} lane={lane} "
                f"target={args.target} head={identity['head']} "
                f"dirty={str(identity['dirty']).lower()}"
            )
            print("full build and full tests are not implied")
            sys.stdout.flush()
            started = time.monotonic()
            completed = subprocess.run(command, env=env)
            receipt = {
                "format": "teamleaderleo-fex-x86-host-dev-receipt-v1",
                "profile": profile_id,
                "requestedProfile": args.profile,
                "lane": lane,
                "target": args.target,
                "head": identity["head"],
                "dirty": identity["dirty"],
                "sourceSwitched": switched,
                "configurationMode": configure_mode,
                "setupElapsedSeconds": round(setup_elapsed, 6),
                "jobs": args.jobs,
                "elapsedSeconds": round(time.monotonic() - started, 6),
                "exitCode": completed.returncode,
                "cacheNamespace": env["CCACHE_NAMESPACE"],
                "ccacheSloppiness": env["CCACHE_SLOPPINESS"],
            }
            write_receipt(receipt_path, receipt)
            print(json.dumps(receipt, sort_keys=True))
            return completed.returncode
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"research dev build: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
