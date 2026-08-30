#!/usr/bin/env bash
set -euo pipefail

test "$(uname -m)" = "aarch64"
test "${RUNNER_ARCH:-}" = "ARM64"
test "$(git -C "${FEX_RESEARCH_SOURCE}" rev-parse HEAD)" = "${FEX_RESEARCH_SOURCE_SHA}"

source_root="${FEX_RESEARCH_SOURCE}"
profile_root="${source_root}/Scripts/ResearchProfiles/gl-resident-companion-v1"
work_root="${RUNNER_TEMP}/fex-gl-resident-companion-v1-work"
build_root="${work_root}/build"
guest_root="${work_root}/guest"
rootfs="${work_root}/rootfs"
host_thunks="${work_root}/host-thunks"
receipts="${FEX_RESEARCH_RECEIPTS}"

mkdir -p "${work_root}" "${host_thunks}"
export DEBIAN_FRONTEND=noninteractive
export FEX_PORTABLE=1

uname -a > "${receipts}/uname.txt"
sudo apt-get update > "${receipts}/apt-update.log" 2>&1
sudo apt-get install -y --no-install-recommends \
  cmake ninja-build clang-18 lld-18 libclang-18-dev llvm-18-dev pkg-config \
  gcc-x86-64-linux-gnu g++-x86-64-linux-gnu binutils \
  libcap-dev libgl-dev libx11-dev libx11-xcb-dev libxcb1-dev libxrandr-dev libxrender-dev xvfb \
  > "${receipts}/apt-install.log" 2>&1
clang++-18 --version > "${receipts}/clang-version.txt"
x86_64-linux-gnu-g++ --version > "${receipts}/guest-compiler-version.txt"

CC=clang-18 CXX=clang++-18 cmake -S "${source_root}" -B "${build_root}" -G Ninja \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo -DUSE_LINKER=lld \
  -DClang_DIR=/usr/lib/llvm-18/lib/cmake/clang \
  -DLLVM_DIR=/usr/lib/llvm-18/lib/cmake/llvm \
  -DENABLE_LTO=OFF -DENABLE_ASSERTIONS=ON -DBUILD_TESTING=OFF \
  -DBUILD_THUNKS=ON -DBUILD_FEXCONFIG=OFF -DRANGES_NATIVE=OFF \
  -DTUNE_CPU=none -DX86_DEV_ROOTFS=/ > "${receipts}/cmake-host.log" 2>&1
cmake --build "${build_root}" --target FEX FEXServer GL-host-64 -- \
  -j"${FEX_RESEARCH_JOBS}" -v > "${receipts}/build-host.log" 2>&1 || {
    tail -n 360 "${receipts}/build-host.log"
    exit 1
  }
cp "${build_root}/HostLibs_64/libGL-host.so" "${host_thunks}/libGL-host.so"

cmake -S "${source_root}/ThunkLibs/GuestLibs" -B "${guest_root}" -G Ninja \
  -DBITNESS=64 -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DCMAKE_TOOLCHAIN_FILE="${source_root}/Data/CMake/toolchain_x86_64.cmake" \
  -DFEX_PROJECT_SOURCE_DIR="${source_root}" \
  -DGENERATOR_EXE="${build_root}/Bin/thunkgen" -DX86_DEV_ROOTFS=/ \
  > "${receipts}/cmake-guest.log" 2>&1
cmake --build "${guest_root}" --target GL-guest GL_bridge-guest -- \
  -j"${FEX_RESEARCH_JOBS}" -v > "${receipts}/build-guest.log" 2>&1 || {
    tail -n 480 "${receipts}/build-guest.log"
    exit 1
  }

readelf -dW "${guest_root}/libGL-guest.so" > "${receipts}/wrapper.dynamic"
readelf -dW "${guest_root}/libfex-GL-bridge.so" > "${receipts}/bridge.dynamic"
! grep -q 'FLAGS_1.*NODELETE' "${receipts}/wrapper.dynamic"
grep -q 'NEEDED.*libfex-GL-bridge.so' "${receipts}/wrapper.dynamic"
grep -q 'FLAGS_1.*NODELETE' "${receipts}/bridge.dynamic"
grep -q 'NEEDED.*libX11.so.6' "${receipts}/bridge.dynamic"
readelf -rW "${guest_root}/libGL-guest.so" > "${receipts}/wrapper.relocations"
readelf -rW "${guest_root}/libfex-GL-bridge.so" > "${receipts}/bridge.relocations"
stat -c 'wrapper_file_bytes=%s' "${guest_root}/libGL-guest.so" > "${receipts}/elf-sizes.txt"
stat -c 'bridge_file_bytes=%s' "${guest_root}/libfex-GL-bridge.so" >> "${receipts}/elf-sizes.txt"
sha256sum "${guest_root}/gen/thunkgen_guest_libGL.inl" \
  "${guest_root}/gen/thunkgen_guest_libGL_bridge.inl" \
  "${guest_root}/gen/thunkgen_guest_libGL_bridge_accessors.inl" \
  > "${receipts}/generated-sha256.txt"
test "$(grep -Ec '^[[:space:]]*MAKE_CALLBACK_THUNK' "${guest_root}/gen/thunkgen_guest_libGL_bridge.inl")" -eq 736
test "$(grep -Ec 'fex_bridge_libGL_invoker_lookup[(]uint32_t index[)]' "${guest_root}/gen/thunkgen_guest_libGL_bridge.inl")" -eq 1
test "$(readelf -Ws "${guest_root}/libGL-guest.so" | awk '$7 == "UND" {print $8}' | sort -u | grep -c '^fex_bridge_libGL_invoker_lookup$')" -eq 1

docker pull --platform=linux/amd64 ubuntu:24.04 > "${receipts}/rootfs-pull.log" 2>&1
container_id="$(docker create --platform=linux/amd64 ubuntu:24.04 /bin/true)"
trap 'docker rm -f "${container_id}" >/dev/null 2>&1 || true' EXIT
mkdir -p "${rootfs}"
docker export "${container_id}" | tar -C "${rootfs}" -xf -
docker rm "${container_id}"
trap - EXIT
mkdir -p "${rootfs}/usr/lib/x86_64-linux-gnu" "${rootfs}/opt/fex-ci"
cp "${guest_root}/libGL-guest.so" "${rootfs}/usr/lib/x86_64-linux-gnu/libGL.so.1"
cp "${guest_root}/libfex-GL-bridge.so" "${rootfs}/usr/lib/x86_64-linux-gnu/libfex-GL-bridge.so"
cp -a /usr/x86_64-linux-gnu/lib/libstdc++.so.6* "${rootfs}/usr/lib/x86_64-linux-gnu/" || true
cp -a /usr/x86_64-linux-gnu/lib/libgcc_s.so.1 "${rootfs}/usr/lib/x86_64-linux-gnu/" || true

x86_64-linux-gnu-gcc -O2 -Wall -Wextra -Werror "${profile_root}/control.c" \
  -o "${rootfs}/opt/fex-ci/control"
x86_64-linux-gnu-gcc -shared -fPIC -O2 -Wall -Wextra -Werror \
  -Wl,-soname,libX11.so.6 "${profile_root}/x11stub.c" \
  -o "${rootfs}/usr/lib/x86_64-linux-gnu/libX11.so.6"
x86_64-linux-gnu-gcc -O2 -Wall -Wextra -Werror \
  "${profile_root}/glprobe.c" -ldl -o "${rootfs}/opt/fex-ci/glprobe"

FEX_ROOTFS="${rootfs}" timeout 20s "${build_root}/Bin/FEX" /opt/fex-ci/control \
  > "${receipts}/control.out" 2> "${receipts}/control.err"
grep -q 'FEX_ORDINARY_CONTROL_OK' "${receipts}/control.out"

Xvfb :99 -screen 0 640x480x24 +extension GLX -nolisten tcp > "${receipts}/xvfb.log" 2>&1 &
xvfb_pid=$!
trap 'kill "${xvfb_pid}" >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 50); do
  test -S /tmp/.X11-unix/X99 && break
  sleep 0.1
done
test -S /tmp/.X11-unix/X99
export DISPLAY=:99
set +e
FEX_ROOTFS="${rootfs}" FEX_THUNKHOSTLIBS="${host_thunks}" timeout 45s \
  "${build_root}/Bin/FEX" /opt/fex-ci/glprobe \
  > "${receipts}/probe.out" 2> "${receipts}/probe.err"
probe_status=$?
set -e
printf '%s\n' "${probe_status}" > "${receipts}/probe.exit"
cat "${receipts}/probe.err"
test "${probe_status}" -eq 0
grep -q 'GL_RESIDENT_COMPANION_RUNTIME_OK' "${receipts}/probe.err"
grep -q 'RESIDENT_GLX_CALLBACK_AFTER_CLOSE_OK' "${receipts}/probe.err"
grep -q 'moved=1' "${receipts}/probe.err"
grep -q 'same_H=1' "${receipts}/probe.err"
grep -q 'BRIDGE_AFTER_CLOSE mappings=' "${receipts}/probe.err"
python3 - "${receipts}/probe.err" <<'PY'
import sys

text = open(sys.argv[1], encoding="utf-8").read()
markers = [
    "POST_CLOSE_GLX_BEGIN",
    "GUEST_XSYNC",
    "GUEST_XDISPLAYSTRING",
    "POST_CLOSE_GLX_END",
    "RESIDENT_GLX_CALLBACK_AFTER_CLOSE_OK",
]
offsets = [text.find(marker) for marker in markers]
if any(offset < 0 for offset in offsets) or offsets != sorted(offsets):
    raise SystemExit(f"bad GLX callback marker order: {list(zip(markers, offsets))}")
PY

python3 - "${receipts}/profile-outcome.json" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(
    json.dumps(
        {
            "schemaVersion": 1,
            "status": "pass",
            "summary": "ordinary FEX control, GL wrapper physical unload, resident companion/X11 persistence, retained GL and GLX calls, and forced moved reload passed",
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
