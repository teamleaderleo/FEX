#!/usr/bin/env bash
set -euo pipefail

test "$(uname -m)" = "aarch64"
test "${RUNNER_ARCH:-}" = "ARM64"
test "$(git -C "${FEX_RESEARCH_SOURCE}" rev-parse HEAD)" = "${FEX_RESEARCH_SOURCE_SHA}"

uname -a > "${FEX_RESEARCH_RECEIPTS}/uname.txt"
python3 --version > "${FEX_RESEARCH_RECEIPTS}/python-version.txt" 2>&1
git --version > "${FEX_RESEARCH_RECEIPTS}/git-version.txt"

python3 - "${FEX_RESEARCH_RECEIPTS}/profile-outcome.json" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(
    json.dumps(
        {
            "schemaVersion": 1,
            "status": "pass",
            "summary": "ARM64 runner, exact source checkout, checked-in profile, and receipt handoff passed",
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
