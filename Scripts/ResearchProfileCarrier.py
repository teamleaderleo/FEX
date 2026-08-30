#!/usr/bin/env python3
"""Run one bounded, checked-in FEX research profile with an exact-source receipt."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = 1
PROFILE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
VARIANT_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_JSON_BYTES = 64 * 1024
MAX_JOBS = 16
MAX_TIMEOUT_SECONDS = 55 * 60
SUPPORTED_PLATFORMS = frozenset({"self-hosted-x86-fex-research", "ubuntu-24.04-arm"})
MANIFEST_KEYS = {
    "schemaVersion",
    "id",
    "title",
    "entrypoint",
    "platform",
    "timeoutSeconds",
    "variants",
}
OUTCOME_KEYS = {"schemaVersion", "status", "summary"}


class ProfileError(RuntimeError):
    """A profile, source, or receipt violated the carrier contract."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_regular_bytes(path: Path, limit: int = MAX_JSON_BYTES) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ProfileError(f"cannot open regular file {path}: {error}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ProfileError(f"expected a regular file: {path}")
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(descriptor, min(16 * 1024, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > limit:
            raise ProfileError(f"file exceeds {limit} bytes: {path}")
        return bytes(data)
    finally:
        os.close(descriptor)


def read_regular_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = read_regular_bytes(path)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProfileError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(value, dict):
        raise ProfileError(f"expected one JSON object in {path}")
    return value, raw


def require_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ProfileError(f"missing profile directory {path}: {error}") from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ProfileError(f"unsafe profile directory: {path}")


def require_regular_file(path: Path) -> bytes:
    return read_regular_bytes(path, limit=1024 * 1024)


def validated_identifier(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not pattern.fullmatch(value):
        raise ProfileError(f"invalid {label}: {value!r}")
    return value


def resolve_profile(source: Path, profile_id: str, variant: str) -> dict[str, Any]:
    validated_identifier(profile_id, PROFILE_ID, "profile id")
    validated_identifier(variant, VARIANT_ID, "variant id")

    scripts = source / "Scripts"
    profiles = scripts / "ResearchProfiles"
    profile_root = profiles / profile_id
    for path in (source, scripts, profiles, profile_root):
        require_directory(path)

    manifest_path = profile_root / "profile.json"
    manifest, manifest_raw = read_regular_json(manifest_path)
    unknown = set(manifest) - MANIFEST_KEYS
    missing = MANIFEST_KEYS - set(manifest)
    if unknown or missing:
        raise ProfileError(
            f"profile manifest key mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    if manifest["schemaVersion"] != SCHEMA_VERSION:
        raise ProfileError("unsupported profile schemaVersion")
    if manifest["id"] != profile_id:
        raise ProfileError("profile manifest id does not match its directory")
    if manifest["entrypoint"] != "run.sh":
        raise ProfileError("profile entrypoint must be run.sh")
    if manifest["platform"] not in SUPPORTED_PLATFORMS:
        raise ProfileError(f"unsupported profile platform: {manifest['platform']!r}")
    if not isinstance(manifest["title"], str) or not (1 <= len(manifest["title"]) <= 200):
        raise ProfileError("profile title must contain 1..200 characters")
    timeout = manifest["timeoutSeconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise ProfileError("profile timeoutSeconds must be an integer")
    if not (1 <= timeout <= MAX_TIMEOUT_SECONDS):
        raise ProfileError(f"profile timeoutSeconds must be within 1..{MAX_TIMEOUT_SECONDS}")
    variants = manifest["variants"]
    if not isinstance(variants, list) or not variants:
        raise ProfileError("profile variants must be a non-empty list")
    if any(not isinstance(item, str) or not VARIANT_ID.fullmatch(item) for item in variants):
        raise ProfileError("profile variants contain an invalid identifier")
    if variants != sorted(set(variants)):
        raise ProfileError("profile variants must be sorted and unique")
    if variant not in variants:
        raise ProfileError(f"variant {variant!r} is not declared by profile {profile_id!r}")

    entrypoint = profile_root / "run.sh"
    entrypoint_raw = require_regular_file(entrypoint)
    return {
        "manifest": manifest,
        "manifestPath": str(manifest_path.relative_to(source)),
        "manifestSha256": sha256_bytes(manifest_raw),
        "entrypoint": entrypoint,
        "entrypointBytes": entrypoint_raw,
        "entrypointPath": str(entrypoint.relative_to(source)),
        "entrypointSha256": sha256_bytes(entrypoint_raw),
    }


def git_output(source: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip() or f"exit {error.returncode}"
        raise ProfileError(f"git {' '.join(arguments)} failed: {detail}") from error
    return completed.stdout


def git_bytes(source: Path, *arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode("utf-8", errors="replace").strip()
        raise ProfileError(
            f"git {' '.join(arguments)} failed: {detail or error.returncode}"
        ) from error
    return completed.stdout


def exact_source_state(source: Path, expected_sha: str) -> dict[str, Any]:
    validated_identifier(expected_sha, FULL_SHA, "source SHA")
    head = git_output(source, "rev-parse", "HEAD").strip()
    if head != expected_sha:
        raise ProfileError(f"source HEAD {head} does not equal requested SHA {expected_sha}")
    worktree_status = git_output(source, "status", "--porcelain=v1", "--untracked-files=normal")
    if worktree_status:
        raise ProfileError("source has tracked, untracked, or submodule changes")
    submodules = git_output(source, "submodule", "status", "--recursive")
    invalid = [line for line in submodules.splitlines() if line and not line.startswith(" ")]
    if invalid:
        raise ProfileError("one or more recursive submodules are uninitialized or off-pin")
    normalized = "\n".join(line.rstrip() for line in submodules.splitlines())
    if normalized:
        normalized += "\n"
    return {
        "head": head,
        "sourceDirty": False,
        "submoduleCount": len(submodules.splitlines()),
        "submoduleInventorySha256": sha256_bytes(normalized.encode("utf-8")),
    }


def committed_profile_state(
    source: Path, expected_sha: str, profile: dict[str, Any]
) -> dict[str, str]:
    result: dict[str, str] = {}
    for label in ("manifest", "entrypoint"):
        path = profile[f"{label}Path"]
        expected_digest = profile[f"{label}Sha256"]
        committed = git_bytes(source, "show", f"{expected_sha}:{path}")
        committed_digest = sha256_bytes(committed)
        if committed_digest != expected_digest:
            raise ProfileError(f"working {label} does not match the file committed at source SHA")
        result[f"{label}BlobSha256"] = committed_digest
    return result


def prepare_receipts(path: Path) -> None:
    if path.exists() or path.is_symlink():
        require_directory(path)
        if any(path.iterdir()):
            raise ProfileError(f"receipt directory must start empty: {path}")
    else:
        try:
            path.mkdir(mode=0o700, parents=False)
        except OSError as error:
            raise ProfileError(f"cannot create receipt directory {path}: {error}") from error


def validate_outcome(path: Path) -> dict[str, Any]:
    outcome, _ = read_regular_json(path)
    if set(outcome) != OUTCOME_KEYS:
        raise ProfileError("profile outcome must contain exactly schemaVersion, status, and summary")
    if outcome["schemaVersion"] != SCHEMA_VERSION:
        raise ProfileError("unsupported profile outcome schemaVersion")
    if outcome["status"] != "pass":
        raise ProfileError("a successful profile process must report status=pass")
    summary = outcome["summary"]
    if not isinstance(summary, str) or not (1 <= len(summary) <= 1000):
        raise ProfileError("profile outcome summary must contain 1..1000 characters")
    return outcome


def write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(temporary, flags, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def execute_profile(
    source: Path,
    entrypoint: bytes,
    environment: dict[str, str],
    timeout_seconds: int,
) -> tuple[int, bool]:
    process = subprocess.Popen(
        ["bash", "--noprofile", "--norc", "-s"],
        cwd=source,
        env=environment,
        stdin=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        process.communicate(input=entrypoint, timeout=timeout_seconds)
        return process.returncode, False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
        return process.returncode, True


def run_profile(args: argparse.Namespace) -> int:
    source = args.source.resolve()
    receipts = Path(os.path.abspath(args.receipts))
    validated_identifier(args.carrier_sha, FULL_SHA, "carrier SHA")
    if not (1 <= args.jobs <= MAX_JOBS):
        raise ProfileError(f"jobs must be within 1..{MAX_JOBS}")
    prepare_receipts(receipts)
    profile = resolve_profile(source, args.profile, args.variant)
    if profile["manifest"]["platform"] != args.platform:
        raise ProfileError(
            f"profile platform {profile['manifest']['platform']!r} does not match runner adapter {args.platform!r}"
        )
    before = exact_source_state(source, args.source_sha)
    profile_state = committed_profile_state(source, args.source_sha, profile)

    started_at = dt.datetime.now(dt.timezone.utc)
    started = time.monotonic()
    environment = os.environ.copy()
    environment.update(
        {
            "FEX_RESEARCH_SOURCE": str(source),
            "FEX_RESEARCH_SOURCE_SHA": args.source_sha,
            "FEX_RESEARCH_PROFILE": args.profile,
            "FEX_RESEARCH_VARIANT": args.variant,
            "FEX_RESEARCH_JOBS": str(args.jobs),
            "FEX_RESEARCH_RECEIPTS": str(receipts),
        }
    )

    exit_code: int | None = None
    status = "profile-failed"
    error: str | None = None
    outcome: dict[str, Any] | None = None
    try:
        exit_code, timed_out = execute_profile(
            source,
            profile["entrypointBytes"],
            environment,
            profile["manifest"]["timeoutSeconds"],
        )
        if timed_out:
            status = "profile-timeout"
            error = "profile exceeded its declared timeout and its process group was terminated"
        elif exit_code == 0:
            outcome = validate_outcome(receipts / "profile-outcome.json")
            status = "pass"
        else:
            error = f"profile exited with status {exit_code}"
    except ProfileError as profile_error:
        status = "invalid-outcome"
        error = str(profile_error)

    after: dict[str, Any] | None = None
    try:
        after = exact_source_state(source, args.source_sha)
    except ProfileError as source_error:
        status = "invalid-source-after-run"
        error = str(source_error)

    finished_at = dt.datetime.now(dt.timezone.utc)
    receipt = {
        "schemaVersion": SCHEMA_VERSION,
        "carrierSha": args.carrier_sha,
        "sourceSha": args.source_sha,
        "profile": args.profile,
        "variant": args.variant,
        "jobs": args.jobs,
        "platform": profile["manifest"]["platform"],
        "timeoutSeconds": profile["manifest"]["timeoutSeconds"],
        "manifestPath": profile["manifestPath"],
        "manifestSha256": profile["manifestSha256"],
        "entrypointPath": profile["entrypointPath"],
        "entrypointSha256": profile["entrypointSha256"],
        "committedProfileState": profile_state,
        "sourceStateBefore": before,
        "sourceStateAfter": after,
        "startedAt": started_at.isoformat(),
        "finishedAt": finished_at.isoformat(),
        "elapsedSeconds": round(time.monotonic() - started, 6),
        "profileExitCode": exit_code,
        "profileOutcome": outcome,
        "status": status,
        "error": error,
    }
    write_json(receipts / "carrier-result.json", receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if status == "pass" else 1


def inspect_profile(args: argparse.Namespace) -> int:
    source = args.source.resolve()
    profile = resolve_profile(source, args.profile, args.variant)
    plan = {
        "schemaVersion": SCHEMA_VERSION,
        "profile": args.profile,
        "variant": args.variant,
        "platform": profile["manifest"]["platform"],
        "timeoutSeconds": profile["manifest"]["timeoutSeconds"],
        "manifestPath": profile["manifestPath"],
        "manifestSha256": profile["manifestSha256"],
        "entrypointPath": profile["entrypointPath"],
        "entrypointSha256": profile["entrypointSha256"],
    }
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    subparsers = argument_parser.add_subparsers(dest="action", required=True)

    inspect = subparsers.add_parser("inspect", help="validate and identify one profile")
    inspect.add_argument("--source", type=Path, default=Path.cwd())
    inspect.add_argument("--profile", required=True)
    inspect.add_argument("--variant", default="default")
    inspect.set_defaults(handler=inspect_profile)

    run = subparsers.add_parser("run", help="run one exact-source profile")
    run.add_argument("--source", type=Path, required=True)
    run.add_argument("--source-sha", required=True)
    run.add_argument("--carrier-sha", required=True)
    run.add_argument("--profile", required=True)
    run.add_argument("--variant", default="default")
    run.add_argument("--platform", choices=sorted(SUPPORTED_PLATFORMS), required=True)
    run.add_argument("--jobs", type=int, default=8)
    run.add_argument("--receipts", type=Path, required=True)
    run.set_defaults(handler=run_profile)
    return argument_parser


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.handler(args)
    except ProfileError as error:
        print(f"research profile refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
