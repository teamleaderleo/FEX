#!/usr/bin/env python3
"""Inventory and retire stale GitHub Actions workflows in the owned FEX fork.

The GitHub Actions API keeps branch-only workflow registrations after an
experiment has finished.  This helper protects workflows present on the
default branch or any open pull-request head, then reports older registrations
that can be disabled.  It is deliberately a dry run unless --apply is given.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable


WORKFLOW_PREFIX = ".github/workflows/"


def gh_json(*args: str) -> Any:
    completed = subprocess.run(
        ["gh", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return json.loads(completed.stdout)


def infer_repo() -> str:
    return gh_json("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]


def workflow_paths(repo: str, treeish: str) -> set[str]:
    tree = gh_json("api", f"repos/{repo}/git/trees/{treeish}?recursive=1")
    return {
        entry["path"]
        for entry in tree.get("tree", [])
        if entry.get("type") == "blob"
        and entry.get("path", "").startswith(WORKFLOW_PREFIX)
        and entry["path"].endswith((".yml", ".yaml"))
    }


def registered_workflows(repo: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for page in range(1, 101):
        payload = gh_json(
            "api", f"repos/{repo}/actions/workflows?per_page=100&page={page}"
        )
        batch = payload.get("workflows", [])
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise RuntimeError("workflow pagination exceeded 10,000 registrations")


def protected_workflow_paths(repo: str, keep_paths: Iterable[str]) -> set[str]:
    metadata = gh_json("api", f"repos/{repo}")
    protected = workflow_paths(repo, metadata["default_branch"])
    pull_requests = gh_json(
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--limit",
        "1000",
        "--json",
        "headRefOid",
    )
    for pull_request in pull_requests:
        protected.update(workflow_paths(repo, pull_request["headRefOid"]))
    protected.update(keep_paths)
    return protected


def parse_before(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "expected an ISO-8601 timestamp, for example 2026-08-16T00:00:00Z"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def retirement_candidates(
    workflows: Iterable[dict[str, Any]],
    protected_paths: set[str],
    before: dt.datetime,
) -> list[dict[str, Any]]:
    candidates = []
    for workflow in workflows:
        created_at = dt.datetime.fromisoformat(
            workflow["created_at"].replace("Z", "+00:00")
        )
        if (
            workflow["state"] == "active"
            and workflow["path"] not in protected_paths
            and created_at < before
        ):
            candidates.append(workflow)
    return sorted(
        candidates, key=lambda workflow: (workflow["created_at"], workflow["path"])
    )


def disable_workflow(repo: str, workflow_id: int) -> None:
    subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "PUT",
            f"repos/{repo}/actions/workflows/{workflow_id}/disable",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", help="owner/name; defaults to the current gh repository"
    )
    parser.add_argument(
        "--before",
        required=True,
        type=parse_before,
        help="only consider workflows created before this ISO-8601 timestamp",
    )
    parser.add_argument(
        "--keep-path",
        action="append",
        default=[],
        help="additional workflow path to protect; may be repeated",
    )
    parser.add_argument(
        "--apply", action="store_true", help="disable the reported candidates"
    )
    parser.add_argument("--json", action="store_true", help="emit the complete plan as JSON")
    args = parser.parse_args()

    repo = args.repo or infer_repo()
    workflows = registered_workflows(repo)
    protected = protected_workflow_paths(repo, args.keep_path)
    candidates = retirement_candidates(workflows, protected, args.before)

    plan = {
        "repo": repo,
        "mode": "apply" if args.apply else "dry-run",
        "before": args.before.isoformat(),
        "registered": len(workflows),
        "protected_paths": len(protected),
        "candidates": [
            {
                "id": workflow["id"],
                "name": workflow["name"],
                "path": workflow["path"],
                "created_at": workflow["created_at"],
            }
            for workflow in candidates
        ],
    }
    if args.json:
        print(json.dumps(plan, indent=2, sort_keys=True))
    else:
        print(
            f"{repo}: {len(workflows)} registered, {len(protected)} protected paths, "
            f"{len(candidates)} retirement candidates before {args.before.isoformat()}"
        )
        for workflow in candidates[:20]:
            print(f"  {workflow['id']}  {workflow['created_at']}  {workflow['path']}")
        if len(candidates) > 20:
            print(f"  ... {len(candidates) - 20} more (use --json for the complete plan)")

    if not args.apply:
        print("Dry run only; pass --apply with the same arguments to disable candidates.")
        return 0

    failures = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(disable_workflow, repo, workflow["id"]): workflow
            for workflow in candidates
        }
        for index, future in enumerate(as_completed(futures), start=1):
            workflow = futures[future]
            try:
                future.result()
            except (OSError, subprocess.CalledProcessError) as exc:
                failures.append((workflow, str(exc)))
            if index % 25 == 0 or index == len(futures):
                print(f"Disabled/attempted {index}/{len(futures)}", file=sys.stderr)

    if failures:
        for workflow, error in failures:
            print(f"FAILED {workflow['id']} {workflow['path']}: {error}", file=sys.stderr)
        return 1
    print(f"Disabled {len(candidates)} stale workflow registrations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
