#!/usr/bin/env python3
"""Keep Vulkan's host-retained executable publications companion-owned."""

from __future__ import annotations

import pathlib
import sys


X11_ROUTES = ("XSync", "XGetVisualInfo", "XDisplayString")


def require(text: str, needle: str, owner: str, failures: list[str]) -> None:
    if needle not in text:
        failures.append(f"{owner}: missing {needle}")


def reject(text: str, needle: str, owner: str, failures: list[str]) -> None:
    if needle in text:
        failures.append(f"{owner}: wrapper-local publication remains: {needle}")


def check(root: pathlib.Path) -> int:
    cmake = (root / "ThunkLibs/GuestLibs/CMakeLists.txt").read_text()
    guest = (root / "ThunkLibs/libvulkan/Guest.cpp").read_text()
    bridge = (root / "ThunkLibs/libvulkan_bridge/Guest.cpp").read_text()
    failures: list[str] = []

    cmake_contract = (
        "generate(libvulkan ${CMAKE_CURRENT_SOURCE_DIR}/../libvulkan/libvulkan_interface.cpp RESIDENT_BRIDGE)",
        'add_guest_lib(vulkan_bridge "libfex-vulkan-bridge.so")',
        'target_link_options(vulkan_bridge-guest PRIVATE "LINKER:-z,nodelete" "LINKER:--no-as-needed")',
        'target_sources(vulkan-guest PRIVATE "${GEN_libvulkan_BRIDGE_ACCESSORS}")',
        "target_link_libraries(vulkan-guest PRIVATE vulkan_bridge-guest)",
        "target_link_libraries(vulkan_bridge-guest PRIVATE PlaceholderX11)",
    )
    for needle in cmake_contract:
        require(cmake, needle, "CMake", failures)

    reject(cmake, 'target_link_options(vulkan-guest PRIVATE "LINKER:-z,nodelete"', "CMake", failures)
    require(guest, '#include "thunkgen_guest_libvulkan_bridge_accessors.inl"', "guest", failures)
    require(guest, "FEXGetResidentCallerForHostFunction(name)", "guest", failures)
    reject(guest, "reinterpret_cast<uintptr_t>(GetCallerForHostFunction(name))", "guest", failures)
    reject(guest, "CallbackUnpack<decltype(", "guest", failures)
    reject(guest, "dlopen(", "guest", failures)
    reject(guest, "dlsym(", "guest", failures)

    for route in X11_ROUTES:
        target = f"FEXVulkanBridge{route}"
        unpacker = f"FEXVulkanBridge{route}Unpacker"
        require(guest, f"(uintptr_t){target}, {unpacker}()", "guest", failures)
        require(bridge, f"{target}(", "bridge", failures)
        require(bridge, f"{unpacker}()", "bridge", failures)
        require(bridge, f"CallbackUnpack<decltype({target})>::Unpack", "bridge", failures)

    print("Vulkan generated invoker authority: resident companion")
    print(f"Vulkan custom X11 publications: {len(X11_ROUTES)} resident companion routes")
    print("Vulkan ordinary wrapper NODELETE: absent")
    if failures:
        print("failures:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("failures: (none)")
    return 0


def main() -> int:
    if len(sys.argv) > 2:
        print(f"usage: {sys.argv[0]} [fex-source-root]", file=sys.stderr)
        return 2
    root = pathlib.Path(sys.argv[1] if len(sys.argv) == 2 else ".").resolve()
    return check(root)


if __name__ == "__main__":
    raise SystemExit(main())
