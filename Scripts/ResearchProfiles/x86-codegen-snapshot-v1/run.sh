#!/usr/bin/env bash
set -euo pipefail

test "$(uname -m)" = "x86_64"
test "${RUNNER_ARCH:-}" = "X64"
test "$(git -C "${FEX_RESEARCH_SOURCE}" rev-parse HEAD)" = "${FEX_RESEARCH_SOURCE_SHA}"

source_root="${FEX_RESEARCH_SOURCE}"
receipts="${FEX_RESEARCH_RECEIPTS}"
lane="actions-x86-${FEX_RESEARCH_PROFILE}-${FEX_RESEARCH_VARIANT}"
last_receipt="${HOME}/.cache/fex-dev/views/${lane}/last-receipt.json"

if [[ -f "${last_receipt}" ]]; then
  mv "${last_receipt}" "${RUNNER_TEMP}/fex-prior-profile-receipt.json"
fi

run_test() {
  local receipt_name="$1"
  local test_name="$2"
  python3 "${source_root}/Scripts/ResearchDevBuild.py" \
    --source "${source_root}" --lane "${lane}" \
    check FEXCore_Tests_CodeCacheConfig "${test_name}" \
    --jobs "${FEX_RESEARCH_JOBS}"
  cp "${last_receipt}" "${receipts}/${receipt_name}.json"
}

run_build() {
  local target="$1"
  python3 "${source_root}/Scripts/ResearchDevBuild.py" \
    --source "${source_root}" --lane "${lane}" \
    build "${target}" --jobs "${FEX_RESEARCH_JOBS}"
  cp "${last_receipt}" "${receipts}/build-${target}.json"
}

run_test ctest-round-trip \
  "CodeCacheConfig - canonical snapshots round-trip atomically.CodeCacheConfig.FEXCore_Tests"
run_test ctest-malformed \
  "CodeCacheConfig - malformed snapshots are rejected without partial application.CodeCacheConfig.FEXCore_Tests"
run_test ctest-host-features \
  "CodeCacheConfig - effective host feature state is reconstructible.CodeCacheConfig.FEXCore_Tests"

run_build FEX
run_build FEXServer
run_build FEXOfflineCompiler

python3 - "${receipts}" "${FEX_RESEARCH_SOURCE_SHA}" <<'PY'
import json
import sys
from pathlib import Path

receipts = Path(sys.argv[1])
source_sha = sys.argv[2]
expected = {
    "ctest-round-trip.json",
    "ctest-malformed.json",
    "ctest-host-features.json",
    "build-FEX.json",
    "build-FEXServer.json",
    "build-FEXOfflineCompiler.json",
}
observed = {path.name for path in receipts.glob("*.json")}
if observed != expected:
    raise SystemExit(f"unexpected helper receipt inventory: {sorted(observed)}")
for name in sorted(expected):
    receipt = json.loads((receipts / name).read_text(encoding="utf-8"))
    if receipt.get("head") != source_sha or receipt.get("exitCode") != 0:
        raise SystemExit(f"invalid exact-source helper receipt: {name}")

(receipts / "profile-outcome.json").write_text(
    json.dumps(
        {
            "schemaVersion": 1,
            "status": "pass",
            "summary": "three exact codegen-snapshot tests and the FEX, FEXServer, and FEXOfflineCompiler affected products passed",
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
