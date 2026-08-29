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
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LANE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
LINUX_TEST_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
PROFILE = "x86-host-dev-v1"
CCACHE_SLOPPINESS = "time_macros"
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
        description="Configure or build one exact FEX target in an isolated stable-path lane."
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
    command = f"git -C {source} submodule update --init --recursive --depth 1"
    raise RuntimeError(f"submodules are uninitialized or not pinned: {', '.join(paths)}; run: {command}")


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
        source = args.source.resolve(strict=True)
        cache_root = args.cache_root.expanduser().resolve()
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
