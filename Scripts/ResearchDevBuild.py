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
PROFILE = "x86-host-dev-v1"
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


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Configure or build one exact FEX target in an isolated stable-path lane."
    )
    result.add_argument("--lane", default="dev", help="isolated stable-path lane name")
    result.add_argument("--source", type=Path, default=REPO_ROOT, help="FEX worktree to expose")
    result.add_argument(
        "--cache-root",
        type=Path,
        default=default_cache_root(),
        help="external build/cache root",
    )
    subparsers = result.add_subparsers(dest="action", required=True)
    subparsers.add_parser("configure", help="freshly configure the lane")
    build = subparsers.add_parser("build", help="build one named CMake target")
    build.add_argument("target", help="exact target, for example vulkan-host-64")
    build.add_argument(
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


def required_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"required tool is missing: {name}")
    return path


def cpu_namespace() -> str:
    model = platform.machine()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name"):
                model += ":" + line.partition(":")[2].strip()
                break
    digest = hashlib.sha256(model.encode("utf-8")).hexdigest()[:12]
    return f"teamleaderleo-FEX:{PROFILE}:{digest}"


def environment(cache_root: Path, lane_root: Path) -> dict[str, str]:
    result = os.environ.copy()
    result.update(
        {
            "CC": required_tool("clang"),
            "CXX": required_tool("clang++"),
            "CCACHE_DIR": str(cache_root / "ccache"),
            "CCACHE_BASEDIR": str(lane_root),
            "CCACHE_NOHASHDIR": "true",
            "CCACHE_NAMESPACE": cpu_namespace(),
        }
    )
    return result


def configure_command(source_view: Path, build: Path) -> list[str]:
    return [
        required_tool("cmake"),
        "--fresh",
        "-S",
        str(source_view),
        "-B",
        str(build),
        "-G",
        "Ninja",
        *CONFIGURE_OPTIONS,
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


def atomic_source_view(source_view: Path, source: Path) -> None:
    temporary = source_view.with_name(f".{source_view.name}.{os.getpid()}.new")
    os.symlink(source, temporary, target_is_directory=True)
    os.replace(temporary, source_view)


def prepare_source_view(
    source: Path,
    source_view: Path,
    build: Path,
    env: dict[str, str],
    runner=subprocess.run,
) -> bool:
    """Expose source at one stable path; clean before switching an existing lane."""
    if source_view.exists() and not source_view.is_symlink():
        raise RuntimeError(f"refusing non-symlink source view: {source_view}")
    current = source_view.resolve() if source_view.is_symlink() else None
    if current == source:
        return False
    if current is not None and (build / "build.ninja").is_file():
        runner(
            [required_tool("cmake"), "--build", str(build), "--target", "clean"],
            check=True,
            env=env,
        )
    atomic_source_view(source_view, source)
    return True


def write_receipt(destination: Path, receipt: dict[str, object]) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.new")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


def expected_profile(cache_namespace: str) -> dict[str, object]:
    return {
        "format": "teamleaderleo-fex-x86-host-dev-profile-v1",
        "profile": PROFILE,
        "configureOptions": CONFIGURE_OPTIONS,
        "cacheNamespace": cache_namespace,
    }


def profile_matches(path: Path, expected: dict[str, object]) -> bool:
    try:
        return json.loads(path.read_text(encoding="utf-8")) == expected
    except (OSError, json.JSONDecodeError):
        return False


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

        for tool in ("ninja", "ccache", "ld.lld", "nasm", "pkg-config"):
            required_tool(tool)
        lane_root.mkdir(parents=True, exist_ok=True)
        build.mkdir(parents=True, exist_ok=True)
        env = environment(cache_root, lane_root)
        with locked_lane(cache_root, lane):
            switched = prepare_source_view(source, source_view, build, env)
            profile = expected_profile(env["CCACHE_NAMESPACE"])
            needs_configure = (
                args.action == "configure"
                or switched
                or not (build / "build.ninja").is_file()
                or not profile_matches(profile_path, profile)
            )
            if needs_configure:
                subprocess.run(configure_command(source_view, build), check=True, env=env)
                write_receipt(profile_path, profile)
            if args.action == "configure":
                print(f"configured lane={lane} source={source} build={build}")
                return 0

            identity = source_identity(source)
            command = build_command(build, args.target, args.jobs)
            print(
                f"scope=FOCUSED_TARGET lane={lane} target={args.target} "
                f"head={identity['head']} dirty={str(identity['dirty']).lower()}"
            )
            print("full build and full tests are not implied")
            sys.stdout.flush()
            started = time.monotonic()
            completed = subprocess.run(command, env=env)
            receipt = {
                "format": "teamleaderleo-fex-x86-host-dev-receipt-v1",
                "profile": PROFILE,
                "lane": lane,
                "target": args.target,
                "head": identity["head"],
                "dirty": identity["dirty"],
                "sourceSwitched": switched,
                "jobs": args.jobs,
                "elapsedSeconds": round(time.monotonic() - started, 6),
                "exitCode": completed.returncode,
                "cacheNamespace": env["CCACHE_NAMESPACE"],
            }
            write_receipt(receipt_path, receipt)
            print(json.dumps(receipt, sort_keys=True))
            return completed.returncode
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"research dev build: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
