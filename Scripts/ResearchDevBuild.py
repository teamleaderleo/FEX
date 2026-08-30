#!/usr/bin/env python3
"""Fast, explicit x86-host build lanes for this owned FEX research fork."""

from __future__ import annotations

import argparse
import contextlib
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
import tempfile
import time
from pathlib import Path

import SubmodulePackCache as submodule_pack_cache
import SubmoduleOriginCache as submodule_origin_cache


REPO_ROOT = Path(__file__).resolve().parents[1]
LANE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
LINUX_TEST_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
PROFILE = "x86-host-dev-v1"
CCACHE_SLOPPINESS = "time_macros"
DEFAULT_SUBMODULE_JOBS = min(os.cpu_count() or 1, 16)
PLAN_REGEN_DESCRIPTION = "FEX plan requires CMake regeneration"
DISCOVERY_QUERY_LIMIT = 128
DISCOVERY_DEFAULT_RESULTS = 32
DISCOVERY_MAX_RESULTS = 64
DISCOVERY_REGISTRY_LIMIT = 32 * 1024 * 1024
EDITOR_GENERATED_TARGETS = ("CONFIG_INC", "IR_INC")
EDITOR_GENERATED_OUTPUTS = (
    Path("include/FEXCore/Config/ConfigValues.inl"),
    Path("include/FEXCore/Config/ConfigOptions.inl"),
    Path("include/FEXCore/IR/IRDefines.inc"),
    Path("include/FEXCore/IR/IRDefines_Dispatch.inc"),
)
DOCTOR_TOOLS = (
    "git",
    "cmake",
    "ctest",
    "ninja",
    "ccache",
    "clang",
    "clang++",
    "ld.lld",
    "nasm",
    "pkg-config",
)
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
        description=(
            "Initialize sources, discover/plan/build one exact FEX target, or run one exact CTest."
        )
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
    submodules.add_argument(
        "--pack-cache",
        action="store_true",
        help="deduplicate immutable shallow pack files in the exact-pin cache generation",
    )
    submodules.add_argument(
        "--origin-cache",
        action="store_true",
        help="populate or reuse self-contained origin-bound shallow repositories",
    )
    subparsers.add_parser(
        "submodule-cache",
        help="inventory submodule pack-cache generations without changing them",
    )
    subparsers.add_parser(
        "submodule-origin-cache",
        help="inventory origin-bound submodule seed generations without changing them",
    )
    subparsers.add_parser(
        "lanes",
        help="inventory stable build lanes without mutating them",
    )
    subparsers.add_parser(
        "doctor",
        help="inspect local focused-development capability without configuring or building",
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
    plan = subparsers.add_parser(
        "plan",
        help="explain pending work for one target without executing target commands",
    )
    plan.add_argument("target", help="exact target, for example vulkan-host-64")
    discover = subparsers.add_parser(
        "discover",
        help="find configured CMake targets and CTests by one literal query",
    )
    discover.add_argument("query", help="case-insensitive literal target/test fragment")
    discover.add_argument(
        "--limit",
        type=positive_int,
        default=DISCOVERY_DEFAULT_RESULTS,
        help=f"maximum results per registry (at most {DISCOVERY_MAX_RESULTS})",
    )
    check = subparsers.add_parser(
        "check",
        help="build one named target and run one exact CTest",
    )
    check.add_argument("target", help="exact owning target, for example thunkgentest")
    check.add_argument(
        "test",
        help="exact CTest name, for example VulkanCustomRouteInventory.ThunkGen",
    )
    check.add_argument(
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


def validate_ctest_name(raw: str) -> str:
    if not raw or len(raw) > 512 or raw != raw.strip():
        raise ValueError("CTest name must be one nonempty exact name of at most 512 characters")
    if not raw.isprintable() or any(character in raw for character in "\r\n\0"):
        raise ValueError("CTest name must be one printable line")
    return raw


def validate_discovery_query(raw: str) -> str:
    if not raw or len(raw) > DISCOVERY_QUERY_LIMIT or raw != raw.strip():
        raise ValueError(
            "discovery query must be one nonempty literal of at most "
            f"{DISCOVERY_QUERY_LIMIT} characters"
        )
    if not raw.isprintable() or any(character in raw for character in "\r\n\0"):
        raise ValueError("discovery query must be one printable line")
    return raw


def validate_discovery_limit(value: int) -> int:
    if value > DISCOVERY_MAX_RESULTS:
        raise ValueError(f"discovery limit must be at most {DISCOVERY_MAX_RESULTS}")
    return value


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


def ninja_plan_command(build: Path, manifest: Path, target: str) -> list[str]:
    if not target or target.startswith("-"):
        raise ValueError("target must be an explicit CMake target name")
    return [
        required_tool("ninja"),
        "-C",
        str(build),
        "-f",
        str(manifest),
        "-n",
        "-d",
        "explain",
        target,
    ]


@contextlib.contextmanager
def shadow_plan_manifest(build: Path):
    """Hide only CMake's already-checked, always-dirty glob edge from Ninja dry-run."""
    manifest = build / "build.ninja"
    try:
        metadata = manifest.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 64 * 1024 * 1024:
            raise RuntimeError(f"unsafe Ninja manifest: {manifest}")
        lines = manifest.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as error:
        raise RuntimeError(f"cannot read Ninja manifest: {manifest}") from error

    descriptor, name = tempfile.mkstemp(prefix=".fex-plan-", suffix=".ninja", dir=build)
    temporary = Path(name)
    try:
        filtered: list[str] = []
        removed_verify_edges = 0
        rerun_edges = 0
        skip_bindings = False
        for line in lines:
            if skip_bindings and line.startswith((" ", "\t")):
                continue
            if skip_bindings:
                skip_bindings = False
            if line.startswith("build ") and ": VERIFY_GLOBS " in line:
                removed_verify_edges += 1
                skip_bindings = True
                continue
            if re.match(r"^build build\.ninja(?=[: ])", line) and ": RERUN_CMAKE " in line:
                rerun_edges += 1
                line = re.sub(
                    r"^build build\.ninja(?=[: ])", f"build {temporary}", line, count=1
                )
                line = line.replace(": RERUN_CMAKE ", ": FEX_PLAN_RERUN_CMAKE ", 1)
            filtered.append(line)
        if removed_verify_edges != 1 or rerun_edges != 1:
            raise RuntimeError(
                "unsupported Ninja regeneration graph: "
                f"verify={removed_verify_edges} rerun={rerun_edges}"
            )

        output = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with output:
            output.write(
                "rule FEX_PLAN_RERUN_CMAKE\n"
                "  command = false\n"
                f"  description = {PLAN_REGEN_DESCRIPTION}\n"
                "  generator = 1\n\n"
            )
            output.writelines(filtered)
        os.utime(temporary, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        yield temporary
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def parse_ninja_plan(stdout: str, stderr: str, sample_limit: int = 64) -> dict[str, object]:
    steps = []
    reasons = []
    for line in stdout.splitlines():
        match = re.match(r"^\[\d+/\d+\]\s+(.*)$", line)
        if match is not None:
            steps.append(match.group(1))
    for line in stderr.splitlines():
        if line.startswith("ninja explain: "):
            reasons.append(line.removeprefix("ninja explain: "))
    return {
        "plannedSteps": len(steps),
        "stepSample": steps[:sample_limit],
        "stepSampleTruncated": len(steps) > sample_limit,
        "reasons": len(reasons),
        "reasonSample": reasons[:sample_limit],
        "reasonSampleTruncated": len(reasons) > sample_limit,
        "requiresCMakeRegeneration": PLAN_REGEN_DESCRIPTION in steps,
    }


def configured_target_registry(manifest: Path) -> dict[str, object]:
    try:
        metadata = manifest.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 64 * 1024 * 1024:
            raise RuntimeError(f"unsafe Ninja manifest: {manifest}")
        payload = manifest.read_bytes()
    except OSError as error:
        raise RuntimeError(f"cannot read Ninja manifest: {manifest}") from error
    if len(payload) != metadata.st_size:
        raise RuntimeError(f"cannot read complete Ninja manifest: {manifest}")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise RuntimeError(f"invalid Ninja manifest encoding: {manifest}") from error

    targets: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"# Object build statements for (\S+) target (.+)", line)
        if match is not None:
            target_type, name = match.groups()
        else:
            match = re.fullmatch(r"# Utility command for (.+)", line)
            if match is None:
                continue
            name, target_type = match.group(1), "UTILITY"
        if not name or len(name) > 512 or not name.isprintable():
            raise RuntimeError("unsupported configured target name")
        previous = targets.setdefault(name, target_type)
        if previous != target_type:
            raise RuntimeError(f"ambiguous configured target type: {name}")
    if not targets:
        raise RuntimeError(f"configured target registry is empty: {manifest}")
    records = [
        {"name": name, "type": targets[name]}
        for name in sorted(targets, key=lambda item: (item.casefold(), item))
    ]
    return {
        "digest": hashlib.sha256(payload).hexdigest(),
        "targets": records,
    }


def ctest_show_only_command(build: Path) -> list[str]:
    return [
        required_tool("ctest"),
        "--test-dir",
        str(build),
        "--show-only=json-v1",
    ]


def configured_test_registry(payload: str) -> dict[str, object]:
    if len(payload.encode("utf-8")) > DISCOVERY_REGISTRY_LIMIT:
        raise RuntimeError("CTest discovery registry exceeded the bounded output limit")
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as error:
        raise RuntimeError("invalid CTest discovery registry JSON") from error
    tests = document.get("tests") if isinstance(document, dict) else None
    if not isinstance(tests, list):
        raise RuntimeError("invalid CTest discovery registry shape")
    records = []
    names: set[str] = set()
    for test in tests:
        if not isinstance(test, dict):
            raise RuntimeError("invalid CTest discovery test record")
        name = test.get("name")
        command = test.get("command")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 512
            or not name.isprintable()
        ):
            raise RuntimeError("invalid CTest discovery test name")
        if name in names:
            raise RuntimeError(f"duplicate CTest discovery test name: {name}")
        names.add(name)
        if command is None:
            state = "not_built"
            command_head = None
        elif (
            isinstance(command, list)
            and command
            and all(isinstance(argument, str) for argument in command)
        ):
            state = "registered"
            command_head = Path(command[0]).name
            if not command_head or not command_head.isprintable():
                raise RuntimeError(f"invalid CTest discovery command head: {name}")
        else:
            raise RuntimeError(f"invalid CTest discovery command: {name}")
        records.append(
            {"name": name, "state": state, "commandHead": command_head}
        )
    if not records:
        raise RuntimeError("CTest discovery registry is empty")
    records.sort(key=lambda record: (str(record["name"]).casefold(), record["name"]))
    return {
        "digest": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "tests": records,
    }


def literal_matches(
    records: list[dict[str, object]], query: str, limit: int
) -> dict[str, object]:
    needle = query.casefold()
    matches = [record for record in records if needle in str(record["name"]).casefold()]
    return {
        "totalMatches": len(matches),
        "returned": min(len(matches), limit),
        "truncated": len(matches) > limit,
        "results": matches[:limit],
    }


def require_plan_lane(
    source: Path,
    source_view: Path,
    build: Path,
    profile_path: Path,
    expected: dict[str, object],
    purpose: str = "plan",
) -> None:
    if not source_view.is_symlink():
        raise RuntimeError(f"{purpose} requires an existing configured lane")
    try:
        current_source = source_view.resolve(strict=True)
    except OSError as error:
        raise RuntimeError(f"{purpose} lane source is unavailable") from error
    if current_source != source:
        raise RuntimeError(
            f"{purpose} refuses to switch or clean a lane; choose its current source"
        )
    if not (build / "build.ninja").is_file():
        raise RuntimeError(f"{purpose} requires an existing configured Ninja graph")
    if not profile_matches(profile_path, expected):
        raise RuntimeError(f"{purpose} requires the lane's exact configured profile")


def require_current_discovery_graph(build: Path, env: dict[str, str]) -> None:
    verify_script = build / "CMakeFiles" / "VerifyGlobs.cmake"
    try:
        verify_metadata = verify_script.stat(follow_symlinks=False)
    except OSError as error:
        raise RuntimeError("discovery requires CMake's generated glob verifier") from error
    if (
        not stat.S_ISREG(verify_metadata.st_mode)
        or verify_metadata.st_size > 16 * 1024 * 1024
    ):
        raise RuntimeError("discovery requires CMake's generated glob verifier")
    subprocess.run(
        [required_tool("cmake"), "-P", str(verify_script)],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    with shadow_plan_manifest(build) as manifest:
        current_graph = subprocess.run(
            ninja_plan_command(build, manifest, str(manifest)),
            env=env,
            capture_output=True,
            text=True,
        )
    if len(current_graph.stdout) + len(current_graph.stderr) > 8 * 1024 * 1024:
        raise RuntimeError("Ninja discovery preflight exceeded the bounded output limit")
    if current_graph.returncode != 0:
        raise RuntimeError("Ninja discovery preflight failed")
    graph_plan = parse_ninja_plan(current_graph.stdout, current_graph.stderr)
    if graph_plan["requiresCMakeRegeneration"]:
        raise RuntimeError(
            "CMake inputs or configured globs changed; regenerate the lane before discovery"
        )


def protected_plan_state(paths: tuple[Path, ...]) -> dict[str, object]:
    result: dict[str, object] = {}
    for path in paths:
        try:
            metadata = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 64 * 1024 * 1024:
                raise RuntimeError(f"unsafe protected plan state: {path}")
            result[path.name] = {
                "bytes": metadata.st_size,
                "mtimeNs": metadata.st_mtime_ns,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        except FileNotFoundError:
            result[path.name] = None
    return result


def ctest_command(build: Path, names: Path) -> list[str]:
    return [
        required_tool("ctest"),
        "--test-dir",
        str(build),
        "--output-on-failure",
        "--no-tests=error",
        "--tests-from-file",
        str(names),
    ]


def generated_ctest_name(line: str) -> str | None:
    command = re.match(r"\s*add_test\s*\(\s*", line, flags=re.IGNORECASE)
    if command is None:
        return None
    rest = line[command.end():]
    if rest.startswith("["):
        bracket = re.match(r"\[(=*)\[(.*?)\]\1\]", rest)
        if bracket is None:
            raise RuntimeError("unsupported generated CTest bracket name")
        name = bracket.group(2)
    elif rest.startswith('"'):
        quoted = re.match(r'"([^"\\]*)"', rest)
        if quoted is None:
            raise RuntimeError("unsupported escaped generated CTest name")
        name = quoted.group(1)
    else:
        bare = re.match(r"([^\s)]+)", rest)
        if bare is None:
            raise RuntimeError("missing generated CTest name")
        name = bare.group(1)
    if not name or name == "NAME" or "$" in name:
        raise RuntimeError(f"unsupported generated CTest name: {name!r}")
    return name


def generated_ctest_registry(build: Path, file_limit: int = 16 * 1024 * 1024) -> dict[str, object]:
    names: list[str] = []
    files = 0
    digest = hashlib.sha256()
    for root, directories, filenames in os.walk(build, followlinks=False):
        directories.sort()
        filenames.sort()
        for filename in filenames:
            if not filename.endswith(".cmake"):
                continue
            path = Path(root) / filename
            try:
                descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            except OSError as error:
                raise RuntimeError(f"cannot safely read generated CTest registry: {path}") from error
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > file_limit:
                    raise RuntimeError(f"unsafe generated CTest registry file: {path}")
                chunks = []
                remaining = file_limit + 1
                while remaining:
                    chunk = os.read(descriptor, remaining)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                payload = b"".join(chunks)
            finally:
                os.close(descriptor)
            if len(payload) != metadata.st_size:
                raise RuntimeError(f"cannot read complete generated CTest registry: {path}")
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as error:
                raise RuntimeError(f"invalid generated CTest registry encoding: {path}") from error
            file_names = [
                name
                for line in text.splitlines()
                if (name := generated_ctest_name(line)) is not None
            ]
            if not file_names:
                continue
            relative = path.relative_to(build).as_posix()
            digest.update(relative.encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(payload).digest())
            files += 1
            names.extend(file_names)
    if not names:
        raise RuntimeError(f"generated CTest registry is empty: {build}")
    return {
        "files": files,
        "definitions": len(names),
        "digest": digest.hexdigest(),
        "names": names,
    }


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


def doctor_git_output(
    git: str,
    source: Path,
    *arguments: str,
    runner=subprocess.run,
) -> subprocess.CompletedProcess[str]:
    return runner(
        [git, "-C", str(source), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )


def doctor_submodules(
    source: Path,
    git: str,
    runner=subprocess.run,
) -> dict[str, object]:
    completed = doctor_git_output(
        git, source, "submodule", "status", "--recursive", runner=runner
    )
    if completed.returncode != 0:
        return {
            "state": "unavailable",
            "reason": "git submodule status failed",
        }

    normalized = []
    uninitialized = []
    drifted = []
    conflicted = []
    for line in completed.stdout.splitlines():
        if len(line) < 43:
            return {"state": "invalid", "reason": "cannot parse recursive submodule status"}
        prefix = line[0]
        path = line[42:].split(" (", 1)[0]
        if not path:
            return {"state": "invalid", "reason": "recursive submodule path is empty"}
        if prefix == "-":
            uninitialized.append(path)
            continue
        if prefix == "+":
            drifted.append(path)
            continue
        if prefix == "U":
            conflicted.append(path)
            continue
        commit = line[1:41]
        if prefix != " " or not re.fullmatch(r"[0-9a-f]{40}", commit):
            return {"state": "invalid", "reason": "cannot parse recursive submodule status"}
        normalized.append(f"{commit} {path}")

    if uninitialized or drifted or conflicted:
        return {
            "state": "not_ready",
            "uninitialized": sorted(uninitialized),
            "drifted": sorted(drifted),
            "conflicted": sorted(conflicted),
            "remediation": (
                f"git -C {source} submodule update --init --recursive --depth 1 "
                f"--jobs {DEFAULT_SUBMODULE_JOBS}"
            ),
        }
    if not normalized:
        return {"state": "invalid", "reason": "recursive submodule status is empty"}
    payload = "\n".join(sorted(normalized)) + "\n"
    return {
        "state": "ready",
        "repositories": len(normalized),
        "pinnedDigest": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def doctor_receipt(
    source: Path,
    *,
    finder=shutil.which,
    runner=subprocess.run,
    machine: str | None = None,
) -> dict[str, object]:
    tools = {
        name: {"state": "ready", "path": path}
        if (path := finder(name)) is not None
        else {"state": "missing"}
        for name in DOCTOR_TOOLS
    }
    missing_tools = [name for name in DOCTOR_TOOLS if tools[name]["state"] == "missing"]
    git = tools["git"].get("path")
    source_receipt: dict[str, object]
    submodules: dict[str, object]
    if not isinstance(git, str):
        source_receipt = {"state": "unavailable", "reason": "git is missing"}
        submodules = {"state": "unavailable", "reason": "git is missing"}
    else:
        head = doctor_git_output(git, source, "rev-parse", "HEAD", runner=runner)
        dirty = doctor_git_output(git, source, "status", "--porcelain", runner=runner)
        if head.returncode != 0 or dirty.returncode != 0:
            source_receipt = {
                "state": "unavailable",
                "reason": "source is not an inspectable Git worktree",
            }
            submodules = {"state": "unavailable", "reason": "source identity is unavailable"}
        else:
            source_receipt = {
                "state": "ready",
                "head": head.stdout.strip(),
                "dirty": bool(dirty.stdout.strip()),
            }
            submodules = doctor_submodules(source, git, runner=runner)

    host_machine = machine if machine is not None else platform.machine()
    x86_host = host_machine in {"x86_64", "amd64"}
    local_blockers = []
    if missing_tools:
        local_blockers.append("missing_tools")
    if source_receipt["state"] != "ready":
        local_blockers.append("source_identity")
    if submodules["state"] != "ready":
        local_blockers.append("submodules")
    if not x86_host:
        local_blockers.append("host_architecture")
    local_preflight_ready = not local_blockers
    dirty_source = source_receipt.get("dirty") is True
    if source_receipt["state"] != "ready":
        exact_head_state = "blocked"
        exact_head_reason = "source identity is unavailable"
    elif dirty_source:
        exact_head_state = "feedback_only"
        exact_head_reason = "source is dirty"
    else:
        exact_head_state = "candidate_requires_post_check"
        exact_head_reason = "recheck clean source identity after the focused command"
    arm_state = (
        "candidate_requires_checked_in_profile"
        if host_machine in {"aarch64", "arm64"}
        else "escalate_to_checked_in_arm64_profile"
    )
    focused_next_commands = (
        [
            "./Scripts/ResearchDevBuild.py --lane NAME build TARGET",
            "./Scripts/ResearchDevBuild.py --lane NAME check TARGET EXACT_CTEST",
            "./Scripts/ResearchDevBuild.py --lane editor editor",
        ]
        if local_preflight_ready
        else []
    )
    return {
        "format": "teamleaderleo-fex-experiment-doctor-v2",
        "status": "preflight_ready" if local_preflight_ready else "blocked",
        "source": source_receipt,
        "submodules": submodules,
        "host": {
            "machine": host_machine,
            "logicalCpus": os.cpu_count(),
        },
        "tools": tools,
        "capabilities": {
            "focusedX86HostBuildAndCTest": {
                "state": "preflight_ready" if local_preflight_ready else "blocked",
                "preflightReady": local_preflight_ready,
                "executionState": "not_run",
                "evidenceState": "not_established",
                "blockers": local_blockers,
                "nextCommands": focused_next_commands,
                "scope": "one named x86-host target or one exact host-side CTest",
                "proof": "required command paths and pinned submodules only; configure/build/test did not run",
            },
            "reusableExactHeadEvidence": {
                "state": exact_head_state,
                "established": False,
                "reason": exact_head_reason,
            },
            "arm64ProductRuntime": {
                "state": arm_state,
                "preflightReady": False,
                "executionState": "not_run",
                "evidenceState": "not_established",
                "scope": "requires an exact-SHA checked-in ARM64 profile and its own runtime oracle",
            },
        },
        "mutation": "none; no configure, build, test, package, cache, or submodule update ran",
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


def retained_graph_clean_command(build: Path) -> list[str]:
    """Clean a dead-source lane without asking CMake to regenerate its graph."""
    manifest = build / "build.ninja"
    try:
        metadata = os.lstat(manifest)
    except OSError as error:
        raise RuntimeError(f"cannot inspect retained Ninja manifest: {manifest}") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_size > 64 * 1024 * 1024
    ):
        raise RuntimeError(f"unsafe retained Ninja manifest: {manifest}")
    return [required_tool("ninja"), "-C", str(build), "-t", "clean"]


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
        old_source_available = current.is_dir()
        for old_build in (*extra_builds, build):
            if (old_build / "build.ninja").is_file():
                command = (
                    [
                        required_tool("cmake"),
                        "--build",
                        str(old_build),
                        "--target",
                        "clean",
                    ]
                    if old_source_available
                    else retained_graph_clean_command(old_build)
                )
                runner(
                    command,
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


def editor_prerequisites_command(build: Path) -> list[str]:
    """Generate the small headers required by most FEX editor parses."""
    return [
        required_tool("cmake"),
        "--build",
        str(build),
        "--target",
        *EDITOR_GENERATED_TARGETS,
        "--parallel",
        str(len(EDITOR_GENERATED_TARGETS)),
    ]


def verify_editor_prerequisites(build: Path) -> None:
    missing = [str(path) for path in EDITOR_GENERATED_OUTPUTS if not (build / path).is_file()]
    if missing:
        raise RuntimeError("editor prerequisite targets omitted outputs: " + ", ".join(missing))


def prepare_editor_database(
    source_view: Path,
    source: Path,
    build: Path,
    destination: Path,
    env: dict[str, str],
    runner=subprocess.run,
) -> tuple[int, float]:
    """Graph-check generated inputs before atomically replacing the editor database."""
    started = time.monotonic()
    runner(
        editor_prerequisites_command(build),
        check=True,
        env=env,
    )
    verify_editor_prerequisites(build)
    elapsed = time.monotonic() - started
    count = write_editor_compile_commands(source_view, source, build, destination)
    return count, elapsed


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
        if args.action == "submodule-cache":
            print(json.dumps(submodule_pack_cache.inventory(cache_root), indent=2, sort_keys=True))
            return 0
        if args.action == "submodule-origin-cache":
            print(json.dumps(submodule_origin_cache.inventory(cache_root), indent=2, sort_keys=True))
            return 0
        source = args.source.resolve(strict=True)
        if args.action == "doctor":
            receipt = doctor_receipt(source)
            print(json.dumps(receipt, indent=2, sort_keys=True))
            return 0 if receipt["status"] == "preflight_ready" else 2
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
            origin_cache = None
            if args.origin_cache:
                origin_cache = submodule_origin_cache.update(
                    source,
                    cache_root,
                    args.jobs,
                    progress=sys.stderr,
                )
            else:
                subprocess.run(
                    submodule_update_command(source, args.jobs),
                    check=True,
                    stdout=sys.stderr,
                )
            require_pinned_submodules(source)
            repositories, digest = pinned_submodule_identity(source)
            pack_cache = None
            defer_pack_cache = bool(
                args.pack_cache
                and origin_cache is not None
                and origin_cache["state"] == "cold_populated"
            )
            if args.pack_cache and not defer_pack_cache:
                pack_cache = submodule_pack_cache.compact(
                    source,
                    cache_root,
                    digest,
                    repositories,
                )
                require_pinned_submodules(source)
                post_repositories, post_digest = pinned_submodule_identity(source)
                if (post_repositories, post_digest) != (repositories, digest):
                    raise RuntimeError("pack-cache compaction changed recursive pin identity")
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
            if pack_cache is not None:
                receipt["packCache"] = pack_cache
            elif defer_pack_cache:
                receipt["packCache"] = {
                    "format": "teamleaderleo-fex-submodule-pack-cache-deferred-v1",
                    "state": "deferred_until_warm_origin",
                    "reason": "avoid retaining one-time network pack variants",
                }
            if origin_cache is not None:
                receipt["originCache"] = origin_cache
            print(json.dumps(receipt, sort_keys=True))
            return 0

        require_pinned_submodules(source)
        for tool in ("ninja", "ccache", "ld.lld", "nasm", "pkg-config"):
            required_tool(tool)
        if args.action not in {"plan", "discover"}:
            lane_root.mkdir(parents=True, exist_ok=True)
            build.mkdir(parents=True, exist_ok=True)
        env = environment(cache_root, lane_root, profile_id)
        profile_marker = expected_profile(
            env["CCACHE_NAMESPACE"], profile_id, configure_options
        )
        if args.action in {"plan", "discover"}:
            purpose = "discovery" if args.action == "discover" else "plan"
            require_plan_lane(
                source,
                source_view,
                build,
                profile_path,
                profile_marker,
                purpose,
            )
        with locked_lane(cache_root, lane):
            if args.action == "discover":
                query = validate_discovery_query(args.query)
                limit = validate_discovery_limit(args.limit)
                require_plan_lane(
                    source,
                    source_view,
                    build,
                    profile_path,
                    profile_marker,
                    "discovery",
                )
                identity = source_identity(source)
                protected_paths = (
                    build / "build.ninja",
                    build / ".ninja_log",
                    build / ".ninja_deps",
                    receipt_path,
                )
                protected_before = protected_plan_state(protected_paths)
                started = time.monotonic()
                require_current_discovery_graph(build, env)

                targets = configured_target_registry(build / "build.ninja")
                ctest = subprocess.run(
                    ctest_show_only_command(build),
                    env=env,
                    capture_output=True,
                    text=True,
                )
                if ctest.returncode != 0:
                    raise RuntimeError("CTest discovery registry enumeration failed")
                ctest_output_bytes = len(ctest.stdout.encode("utf-8")) + len(
                    ctest.stderr.encode("utf-8")
                )
                if ctest_output_bytes > DISCOVERY_REGISTRY_LIMIT:
                    raise RuntimeError("CTest discovery registry exceeded the bounded output limit")
                tests = configured_test_registry(ctest.stdout)

                protected_after = protected_plan_state(protected_paths)
                if protected_after != protected_before:
                    raise RuntimeError("discovery changed Ninja or last-receipt state")
                if source_identity(source) != identity:
                    raise RuntimeError("source identity changed during discovery")
                elapsed = time.monotonic() - started
                receipt = {
                    "format": "teamleaderleo-fex-configured-discovery-v1",
                    "profile": profile_id,
                    "requestedProfile": args.profile,
                    "lane": lane,
                    "head": identity["head"],
                    "dirty": identity["dirty"],
                    "query": query,
                    "limit": limit,
                    "targets": literal_matches(targets["targets"], query, limit),
                    "tests": literal_matches(tests["tests"], query, limit),
                    "registries": {
                        "targets": len(targets["targets"]),
                        "targetDigest": targets["digest"],
                        "tests": len(tests["tests"]),
                        "testDigest": tests["digest"],
                    },
                    "elapsedSeconds": round(elapsed, 6),
                    "configurationMode": "reuse",
                    "protectedStateUnchanged": True,
                    "execution": {
                        "configured": False,
                        "targetCommands": False,
                        "tests": False,
                    },
                    "ownershipInference": "none; target and test matches are separate registries",
                    "mutation": (
                        "CMake glob sentinel may update; target outputs, Ninja state, "
                        "and build receipt are unchanged"
                    ),
                }
                print(json.dumps(receipt, sort_keys=True))
                return 0
            if args.action == "plan":
                require_plan_lane(source, source_view, build, profile_path, profile_marker)
                identity = source_identity(source)
                protected_paths = (
                    build / ".ninja_log",
                    build / ".ninja_deps",
                    receipt_path,
                )
                protected_before = protected_plan_state(protected_paths)
                verify_script = build / "CMakeFiles" / "VerifyGlobs.cmake"
                try:
                    verify_metadata = verify_script.stat(follow_symlinks=False)
                except OSError as error:
                    raise RuntimeError("plan requires CMake's generated glob verifier") from error
                if (
                    not stat.S_ISREG(verify_metadata.st_mode)
                    or verify_metadata.st_size > 16 * 1024 * 1024
                ):
                    raise RuntimeError("plan requires CMake's generated glob verifier")
                glob_started = time.monotonic()
                subprocess.run(
                    [required_tool("cmake"), "-P", str(verify_script)],
                    check=True,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                glob_elapsed = time.monotonic() - glob_started
                plan_started = time.monotonic()
                with shadow_plan_manifest(build) as manifest:
                    completed = subprocess.run(
                        ninja_plan_command(build, manifest, args.target),
                        env=env,
                        capture_output=True,
                        text=True,
                    )
                plan_elapsed = time.monotonic() - plan_started
                protected_after = protected_plan_state(protected_paths)
                if protected_after != protected_before:
                    raise RuntimeError("target plan changed Ninja or last-receipt state")
                post_identity = source_identity(source)
                if post_identity != identity:
                    raise RuntimeError("source identity changed while planning")
                if len(completed.stdout) + len(completed.stderr) > 8 * 1024 * 1024:
                    raise RuntimeError("Ninja target plan output exceeded the bounded receipt limit")
                if completed.returncode != 0:
                    detail = (completed.stderr or completed.stdout).strip().splitlines()
                    raise RuntimeError(
                        f"Ninja target plan failed: {detail[-1] if detail else completed.returncode}"
                    )
                plan = parse_ninja_plan(completed.stdout, completed.stderr)
                if plan["requiresCMakeRegeneration"]:
                    raise RuntimeError(
                        "CMake inputs or configured globs changed; regenerate the lane before planning"
                    )
                normalized_plan = replace_path(plan, str(source_view), "$SOURCE")
                normalized_plan = replace_path(normalized_plan, str(build), "$BUILD")
                receipt = {
                    "format": "teamleaderleo-fex-x86-host-plan-receipt-v1",
                    "profile": profile_id,
                    "requestedProfile": args.profile,
                    "lane": lane,
                    "target": args.target,
                    "head": identity["head"],
                    "dirty": identity["dirty"],
                    "configurationMode": "reuse",
                    "globCheckElapsedSeconds": round(glob_elapsed, 6),
                    "planElapsedSeconds": round(plan_elapsed, 6),
                    "plan": normalized_plan,
                    "protectedStateUnchanged": True,
                    "targetCommandsExecuted": False,
                    "mutation": "CMake glob sentinel may update; target outputs and build receipt are unchanged",
                }
                print(json.dumps(receipt, sort_keys=True))
                return 0
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
                destination = source / "compile_commands.json"
                count, prerequisites_elapsed = prepare_editor_database(
                    source_view, source, build, destination, env
                )
                print(
                    f"editor lane={lane} entries={count} "
                    f"configuration={configure_mode} "
                    f"generated_headers={len(EDITOR_GENERATED_OUTPUTS)} "
                    f"prerequisites_seconds={prerequisites_elapsed:.6f} "
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
            if args.action == "check":
                test = validate_ctest_name(args.test)
                print(
                    f"scope=FOCUSED_CTEST profile={args.profile} lane={lane} "
                    f"target={args.target} test={test} head={identity['head']} "
                    f"dirty={str(identity['dirty']).lower()}"
                )
                print("one exact CTest is selected; other targets and tests are not implied")
                sys.stdout.flush()
                started = time.monotonic()
                build_started = time.monotonic()
                completed = subprocess.run(command, env=env)
                build_elapsed = time.monotonic() - build_started
                selection_elapsed = 0.0
                test_elapsed = 0.0
                selected_tests: list[str] = []
                registry_receipt: dict[str, object] | None = None
                if completed.returncode == 0:
                    with tempfile.TemporaryDirectory(
                        prefix=".focused-ctest-", dir=lane_root
                    ) as temporary:
                        temporary_root = Path(temporary)
                        names = temporary_root / "tests.txt"
                        names.write_text(test + "\n", encoding="utf-8")
                        selection_started = time.monotonic()
                        registry = generated_ctest_registry(build)
                        selection_elapsed = time.monotonic() - selection_started
                        registry_names = registry.pop("names")
                        registry_receipt = registry
                        selected_tests = [
                            name for name in registry_names if name == test
                        ]
                        if selected_tests != [test]:
                            print(
                                "research dev build: exact CTest selection mismatch: "
                                f"{selected_tests!r}",
                                file=sys.stderr,
                            )
                            completed = subprocess.CompletedProcess(command, 2)
                        else:
                            test_started = time.monotonic()
                            completed = subprocess.run(
                                ctest_command(build, names), env=env
                            )
                            test_elapsed = time.monotonic() - test_started
                receipt = {
                    "format": "teamleaderleo-fex-x86-host-check-receipt-v1",
                    "profile": profile_id,
                    "requestedProfile": args.profile,
                    "lane": lane,
                    "target": args.target,
                    "test": test,
                    "selectedTests": selected_tests,
                    "testRegistry": registry_receipt,
                    "head": identity["head"],
                    "dirty": identity["dirty"],
                    "sourceSwitched": switched,
                    "configurationMode": configure_mode,
                    "setupElapsedSeconds": round(setup_elapsed, 6),
                    "jobs": args.jobs,
                    "buildElapsedSeconds": round(build_elapsed, 6),
                    "selectionElapsedSeconds": round(selection_elapsed, 6),
                    "testElapsedSeconds": round(test_elapsed, 6),
                    "elapsedSeconds": round(time.monotonic() - started, 6),
                    "exitCode": completed.returncode,
                    "cacheNamespace": env["CCACHE_NAMESPACE"],
                    "ccacheSloppiness": env["CCACHE_SLOPPINESS"],
                }
                write_receipt(receipt_path, receipt)
                print(json.dumps(receipt, sort_keys=True))
                return completed.returncode
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
