#!/usr/bin/env python3

from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one replacement anchor, found {count}")
    path.write_text(text.replace(old, new, 1))


root = Path(__file__).resolve().parents[2]
cmake = root / "ThunkLibs/GuestLibs/CMakeLists.txt"

old = '''  generate(libvulkan ${CMAKE_CURRENT_SOURCE_DIR}/../libvulkan/libvulkan_interface.cpp)
  target_include_directories(libvulkan-guest-deps INTERFACE ${FEX_PROJECT_SOURCE_DIR}/External/Vulkan-Headers/include/)
  target_include_directories_from_pkgconfig(libvulkan-guest-deps "xcb;x11;xrandr;xrender")

  # Derive the resident bridge signature set from the normal generated Vulkan
  # guest thunk output, keeping the ordinary interface as the single source of truth.
  set(VULKAN_GUEST_INL "${CMAKE_CURRENT_BINARY_DIR}/gen/thunkgen_guest_libvulkan.inl")
  set(VULKAN_BRIDGE_INL "${CMAKE_CURRENT_BINARY_DIR}/gen/thunkgen_guest_libvulkan_bridge.inl")
  set(VULKAN_BRIDGE_ACCESSORS "${CMAKE_CURRENT_BINARY_DIR}/gen/thunkgen_guest_libvulkan_bridge_accessors.inl")
  add_custom_command(
    OUTPUT "${VULKAN_BRIDGE_INL}" "${VULKAN_BRIDGE_ACCESSORS}"
    DEPENDS "${VULKAN_GUEST_INL}" "${FEX_PROJECT_SOURCE_DIR}/ThunkLibs/Generator/extract_guest_bridge.py"
    COMMAND "${Python3_EXECUTABLE}"
      "${FEX_PROJECT_SOURCE_DIR}/ThunkLibs/Generator/extract_guest_bridge.py"
      "${VULKAN_GUEST_INL}" "${VULKAN_BRIDGE_INL}" "${VULKAN_BRIDGE_ACCESSORS}"
    VERBATIM)
  add_custom_target(vulkan-bridge-generated DEPENDS "${VULKAN_BRIDGE_INL}" "${VULKAN_BRIDGE_ACCESSORS}")

  add_library(libvulkan_bridge-guest-deps INTERFACE)
  target_include_directories(libvulkan_bridge-guest-deps INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/../include")
  target_include_directories(libvulkan_bridge-guest-deps INTERFACE ${FEX_PROJECT_SOURCE_DIR}/External/Vulkan-Headers/include/)
  target_include_directories_from_pkgconfig(libvulkan_bridge-guest-deps "xcb;x11;xrandr;xrender")
  set(GEN_libvulkan_bridge "${VULKAN_BRIDGE_INL}" "${VULKAN_BRIDGE_ACCESSORS}")
  add_guest_lib(vulkan_bridge "libfex-vulkan-bridge.so")
  set_target_properties(vulkan_bridge-guest PROPERTIES OUTPUT_NAME "fex-vulkan-bridge")
  target_link_options(vulkan_bridge-guest PRIVATE "LINKER:-z,nodelete")
  add_dependencies(vulkan_bridge-guest vulkan-bridge-generated)

  add_guest_lib(vulkan "libvulkan.so.1")
  target_link_libraries(vulkan-guest PRIVATE vulkan_bridge-guest)
  add_dependencies(vulkan-guest vulkan-bridge-generated)
'''

new = '''  generate(libvulkan ${CMAKE_CURRENT_SOURCE_DIR}/../libvulkan/libvulkan_interface.cpp RESIDENT_BRIDGE)
  target_include_directories(libvulkan-guest-deps INTERFACE ${FEX_PROJECT_SOURCE_DIR}/External/Vulkan-Headers/include/)
  target_include_directories_from_pkgconfig(libvulkan-guest-deps "xcb;x11;xrandr;xrender")

  # The normal wrapper, resident invokers, and resident callback accessors now
  # come from one thunkgen analysis/invocation.
  add_library(libvulkan_bridge-guest-deps INTERFACE)
  target_include_directories(libvulkan_bridge-guest-deps INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/../include")
  target_include_directories(libvulkan_bridge-guest-deps INTERFACE ${FEX_PROJECT_SOURCE_DIR}/External/Vulkan-Headers/include/)
  target_include_directories_from_pkgconfig(libvulkan_bridge-guest-deps "xcb;x11;xrandr;xrender")
  set(GEN_libvulkan_bridge "${GEN_libvulkan_BRIDGE}")
  add_guest_lib(vulkan_bridge "libfex-vulkan-bridge.so")
  set_target_properties(vulkan_bridge-guest PROPERTIES OUTPUT_NAME "fex-vulkan-bridge")
  target_link_options(vulkan_bridge-guest PRIVATE "LINKER:-z,nodelete")

  add_guest_lib(vulkan "libvulkan.so.1")
  target_link_libraries(vulkan-guest PRIVATE vulkan_bridge-guest)
'''

replace_once(cmake, old, new)
print("Converted Vulkan resident bridge to first-class thunkgen outputs")
