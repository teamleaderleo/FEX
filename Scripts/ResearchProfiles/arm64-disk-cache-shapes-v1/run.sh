#!/usr/bin/env bash
set -euo pipefail

test "$(uname -m)" = "aarch64"
test "${RUNNER_ARCH:-}" = "ARM64"
test "$(git -C "${FEX_RESEARCH_SOURCE}" rev-parse HEAD)" = "${FEX_RESEARCH_SOURCE_SHA}"

source_root="${FEX_RESEARCH_SOURCE}"
profile_root="${source_root}/Scripts/ResearchProfiles/arm64-disk-cache-shapes-v1"
work_root="${RUNNER_TEMP}/fex-arm64-disk-cache-shapes-v1-work"
build_root="${work_root}/build"
rootfs="${work_root}/rootfs"
cache_root="${work_root}/cache"
receipts="${FEX_RESEARCH_RECEIPTS}"
guest_path="${rootfs}/opt/fex-ci/disk-cache-guest"
active_pid=""

cleanup() {
  if test -n "${active_pid}" && kill -0 "${active_pid}" 2>/dev/null; then
    kill "${active_pid}" 2>/dev/null || true
    wait "${active_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

mkdir -p "${work_root}" "${rootfs}/opt/fex-ci" "${cache_root}"
export DEBIAN_FRONTEND=noninteractive
export FEX_PORTABLE=1

uname -a > "${receipts}/uname.txt"
# The runner owns the receipts directory; sudo is needed only by apt itself.
# shellcheck disable=SC2024
sudo apt-get update > "${receipts}/apt-update.log" 2>&1
# shellcheck disable=SC2024
sudo apt-get install -y --no-install-recommends \
  cmake ninja-build clang-18 lld-18 libclang-18-dev llvm-18-dev pkg-config \
  gcc-x86-64-linux-gnu binutils libcap-dev \
  > "${receipts}/apt-install.log" 2>&1
clang++-18 --version > "${receipts}/clang-version.txt"
x86_64-linux-gnu-gcc --version > "${receipts}/guest-compiler-version.txt"

CC=clang-18 CXX=clang++-18 cmake -S "${source_root}" -B "${build_root}" -G Ninja \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo -DUSE_LINKER=lld \
  -DClang_DIR=/usr/lib/llvm-18/lib/cmake/clang \
  -DLLVM_DIR=/usr/lib/llvm-18/lib/cmake/llvm \
  -DENABLE_LTO=OFF -DENABLE_ASSERTIONS=ON -DBUILD_TESTING=OFF \
  -DBUILD_THUNKS=OFF -DBUILD_FEXCONFIG=OFF -DRANGES_NATIVE=OFF \
  -DTUNE_CPU=none -DX86_DEV_ROOTFS=/ > "${receipts}/cmake-host.log" 2>&1
cmake --build "${build_root}" --target FEX FEXServer -- \
  -j"${FEX_RESEARCH_JOBS}" -v > "${receipts}/build-host.log" 2>&1 || {
    tail -n 360 "${receipts}/build-host.log"
    exit 1
  }

x86_64-linux-gnu-gcc -nostdlib -static -fno-pie -no-pie -fno-stack-protector \
  -O2 -Wall -Wextra -Werror "${profile_root}/guest.c" -o "${guest_path}"
readelf -hW "${guest_path}" > "${receipts}/guest.elf-header"
readelf -lW "${guest_path}" > "${receipts}/guest.program-headers"
sha256sum "${guest_path}" > "${receipts}/guest-sha256.txt"

FEX_ROOTFS="${rootfs}" FEX_DISKCACHE=0 \
  "${build_root}/Bin/FEX" /opt/fex-ci/disk-cache-guest \
  > "${receipts}/control.out" 2> "${receipts}/control.err"
grep -q 'FEX_DISK_CACHE_GUEST_OK' "${receipts}/control.out"

read_live_stats() {
  local label="$1"
  local expected="$2"
  python3 - "${active_pid}" "${receipts}/${label}-stats.json" "${expected}" <<'PY'
import json
import struct
import sys
import time
from pathlib import Path

pid, output, expected = int(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
path = Path(f"/dev/shm/fex-{pid}-stats")
header = struct.Struct("<BBH48sIII")
slot = struct.Struct("<II13Q")
deadline = time.monotonic() + 15
latest = None
while time.monotonic() < deadline:
    try:
        contents = path.read_bytes()
    except FileNotFoundError:
        time.sleep(0.05)
        continue
    if len(contents) < header.size:
        time.sleep(0.05)
        continue
    version, app_type, slot_bytes, revision, head, mapped_bytes, _ = header.unpack_from(contents)
    if version != 2 or app_type != 1 or slot_bytes != slot.size:
        raise SystemExit(f"unexpected live-stats ABI: {(version, app_type, slot_bytes)}")
    if mapped_bytes > len(contents):
        time.sleep(0.05)
        continue
    counters = {"jitCount": 0, "diskHits": 0, "diskMisses": 0, "diskLookupCycles": 0}
    tids = []
    visited = set()
    offset = head
    while offset:
        if offset in visited or len(visited) >= 1024 or offset + slot.size > mapped_bytes:
            raise SystemExit(f"invalid live-stats linked list at offset {offset}")
        visited.add(offset)
        values = slot.unpack_from(contents, offset)
        next_offset, tid, *accumulated = values
        if tid:
            tids.append(tid)
            counters["jitCount"] += accumulated[8]
            counters["diskHits"] += accumulated[9]
            counters["diskMisses"] += accumulated[10]
            counters["diskLookupCycles"] += accumulated[11]
        offset = next_offset
    latest = {
        "format": "teamleaderleo-fex-live-cache-stats-v1",
        "fexRevision": revision.rstrip(b"\0").decode("utf-8", "replace"),
        "hostPid": pid,
        "threadIds": tids,
        **counters,
    }
    satisfied = (
        expected == "cold" and counters["jitCount"] > 0 and counters["diskMisses"] > 0
    ) or (expected == "warm" and counters["diskHits"] > 0)
    if satisfied:
        output.write_text(json.dumps(latest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        break
    time.sleep(0.05)
else:
    raise SystemExit(f"live-stats oracle not satisfied for {expected}: {latest}")
PY
}

run_profiled() {
  local label="$1"
  local expected="$2"
  FEX_ROOTFS="${rootfs}" FEX_DISKCACHE=1 FEX_DISKCACHEPATH="${cache_root}/" \
    FEX_PROFILESTATS=1 "${build_root}/Bin/FEX" /opt/fex-ci/disk-cache-guest \
    > "${receipts}/${label}.out" 2> "${receipts}/${label}.err" &
  active_pid=$!
  read_live_stats "${label}" "${expected}"
  for _ in $(seq 1 200); do
    if ! kill -0 "${active_pid}" 2>/dev/null; then
      break
    fi
    sleep 0.05
  done
  if kill -0 "${active_pid}" 2>/dev/null; then
    echo "${label} FEX process exceeded its bounded guest lifetime" >&2
    return 1
  fi
  wait "${active_pid}"
  active_pid=""
  grep -q 'FEX_DISK_CACHE_GUEST_OK' "${receipts}/${label}.out"
}

run_profiled cold cold
python3 "${source_root}/Scripts/AnalyzeDiskCache.py" "${cache_root}/RWCacheDB" \
  > "${receipts}/cache-shapes.json"
python3 - "${receipts}/cache-shapes.json" <<'PY'
import json
import sys

result = json.load(open(sys.argv[1], encoding="utf-8"))
if result["ordinaryRecordCount"] <= 0:
    raise SystemExit("cold producer created no ordinary disk-cache records")
if not result["minimalPayloads"]:
    raise SystemExit("current producer unexpectedly stored trailing blob bytes")
if any(record["storedBytes"] != record["requiredBytes"] for record in result["records"]):
    raise SystemExit("stored and reader-required blob sizes disagree")
PY
stat -c '%n %s' "${cache_root}/RWCacheDB.foz" "${cache_root}/RWCacheDB_idx.foz" \
  > "${receipts}/cache-before-warm.sizes"
sha256sum "${cache_root}/RWCacheDB.foz" "${cache_root}/RWCacheDB_idx.foz" \
  > "${receipts}/cache-before-warm.sha256"

run_profiled warm warm
stat -c '%n %s' "${cache_root}/RWCacheDB.foz" "${cache_root}/RWCacheDB_idx.foz" \
  > "${receipts}/cache-after-warm.sizes"
sha256sum "${cache_root}/RWCacheDB.foz" "${cache_root}/RWCacheDB_idx.foz" \
  > "${receipts}/cache-after-warm.sha256"
cmp "${receipts}/cache-before-warm.sizes" "${receipts}/cache-after-warm.sizes"
cmp "${receipts}/cache-before-warm.sha256" "${receipts}/cache-after-warm.sha256"

python3 - "${receipts}/cache-shapes.json" "${receipts}/profile-outcome.json" <<'PY'
import json
import sys
from pathlib import Path

shapes = json.load(open(sys.argv[1], encoding="utf-8"))
Path(sys.argv[2]).write_text(
    json.dumps(
        {
            "schemaVersion": 1,
            "status": "pass",
            "summary": "cache-disabled control, positive cold JIT/misses, strict minimal-blob parsing, positive fresh-process warm hits, and immutable warm cache passed",
            "ordinaryRecordCount": shapes["ordinaryRecordCount"],
            "storedBlobBytes": shapes["totals"]["storedBlobBytes"],
            "guestBytes": shapes["totals"]["guestBytes"],
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
