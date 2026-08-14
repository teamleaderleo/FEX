cmake_minimum_required(VERSION 3.14)
project(guest-thunks)
include(${FEX_PROJECT_SOURCE_DIR}/Data/CMake/version_to_variables.cmake)

option(ENABLE_CLANG_THUNKS "Enable building thunks with clang" FALSE)

find_package(PkgConfig REQUIRED)

# Use include directories from pkg-config and fall back to global defaults if not available.
# /usr/include is dropped to avoid mixing foreign-architecture standard library headers.
function(target_include_directories_from_pkgconfig target pkg)
  pkg_check_modules(PKG QUIET ${pkg})
  if (PKG_FOUND)
    list(REMOVE_ITEM PKG_INCLUDE_DIRS /usr/include)
    target_include_directories(${target} INTERFACE ${PKG_INCLUDE_DIRS})
  elseif (ARGN)
    target_include_directories(${target} INTERFACE ${ARGN})
  endif()
endfunction()

if (ENABLE_CLANG_THUNKS)
  set(LD_OVERRIDE "-fuse-ld=lld")
  add_link_options(${LD_OVERRIDE})
endif()

if (NOT X86_DEV_ROOTFS)
  message(FATAL_ERROR "X86_DEV_ROOTFS must be set(use \"/\" to ignore)")
endif()

find_program(CCACHE_PROGRAM ccache)
if (CCACHE_PROGRAM)
  message(STATUS "CCache enabled for guest thunks")
  set_property(GLOBAL PROPERTY RULE_LAUNCH_COMPILE "${CCACHE_PROGRAM}")
endif()

if (CMAKE_CURRENT_SOURCE_DIR STREQUAL CMAKE_SOURCE_DIR)
  set(CMAKE_CXX_STANDARD 20)
  set(DATA_DIRECTORY "" CACHE PATH "Global data directory (override)")
  if (NOT DATA_DIRECTORY)
    set(DATA_DIRECTORY "${CMAKE_INSTALL_PREFIX}/share/fex-emu")
  endif()
  set(TARGET_TYPE SHARED)
  set(GENERATE_GUEST_INSTALL_TARGETS TRUE)
  if(NOT TARGET uninstall)
    configure_file("${FEX_PROJECT_SOURCE_DIR}/Data/CMake/cmake_uninstall.cmake.in"
      "${CMAKE_CURRENT_BINARY_DIR}/CMakeFiles/cmake_uninstall.cmake"
      IMMEDIATE @ONLY)
    add_custom_target(uninstall
      COMMAND ${CMAKE_COMMAND} -P ${CMAKE_CURRENT_BINARY_DIR}/CMakeFiles/cmake_uninstall.cmake)
  endif()
else()
  set(GENERATOR_EXE thunkgen)
  set(TARGET_TYPE OBJECT)
  set(GENERATE_GUEST_INSTALL_TARGETS FALSE)
  set(BITNESS 64)
endif()

function(generate NAME SOURCE_FILE)
  add_library(${NAME}-guest-deps INTERFACE)
  target_include_directories(${NAME}-guest-deps INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/../include")
  if (BITNESS EQUAL 32)
    target_compile_definitions(${NAME}-guest-deps INTERFACE IS_32BIT_THUNK)
  endif()
  set(prop "$<TARGET_PROPERTY:${NAME}-guest-deps,INTERFACE_INCLUDE_DIRECTORIES>")
  set(compile_prop "$<TARGET_PROPERTY:${NAME}-guest-deps,INTERFACE_COMPILE_DEFINITIONS>")

  set(OUTFOLDER "${CMAKE_CURRENT_BINARY_DIR}/gen")
  set(OUTFILE "${OUTFOLDER}/thunkgen_guest_${NAME}.inl")
  file(MAKE_DIRECTORY "${OUTFOLDER}")

  if (BITNESS EQUAL 32)
    set(BITNESS_FLAGS "-for-32bit-guest")
    set(BITNESS_FLAGS2 "-m32" "--target=i686-linux-gnu")
  else()
    set(BITNESS_FLAGS "")
    set(BITNESS_FLAGS2 "--target=x86_64-linux-gnu")
  endif()
  list(APPEND BITNESS_FLAGS2 "--sysroot" "${X86_DEV_ROOTFS}")

  add_custom_command(
    OUTPUT "${OUTFILE}"
    DEPENDS "${GENERATOR_EXE}"
    DEPENDS "${SOURCE_FILE}"
    COMMAND "${GENERATOR_EXE}" "${SOURCE_FILE}" "${NAME}" "-guest" "${OUTFILE}" "${X86_DEV_ROOTFS}" ${BITNESS_FLAGS} -- -std=c++20 ${BITNESS_FLAGS2}
      "$<$<BOOL:${compile_prop}>:;-D$<JOIN:${compile_prop},;-D>>"
      "$<$<BOOL:${prop}>:;-isystem$<JOIN:${prop},;-isystem>>"
    VERBATIM
    COMMAND_EXPAND_LISTS)

  list(APPEND OUTPUTS "${OUTFILE}")
  set(GEN_${NAME} ${OUTPUTS} PARENT_SCOPE)
endfunction()

function(add_guest_lib NAME SONAME)
  set(SOURCE_FILE ../lib${NAME}/lib${NAME}_Guest.cpp)
  get_filename_component(SOURCE_FILE_ABS "${SOURCE_FILE}" ABSOLUTE)
  set(SOURCE_LDS_FILE ../lib${NAME}/lib${NAME}_Guest.lds)
  get_filename_component(SOURCE_LDS_FILE_ABS "${SOURCE_LDS_FILE}" ABSOLUTE)
  set(SOURCE_LDS_32_FILE ../lib${NAME}/lib${NAME}_Guest_32.lds)
  get_filename_component(SOURCE_LDS_32_FILE_ABS "${SOURCE_LDS_32_FILE}" ABSOLUTE)

  if (NOT EXISTS "${SOURCE_FILE_ABS}")
    set(SOURCE_FILE ../lib${NAME}/Guest.cpp)
    get_filename_component(SOURCE_FILE_ABS "${SOURCE_FILE}" ABSOLUTE)
    if (NOT EXISTS "${SOURCE_FILE_ABS}")
      message (FATAL_ERROR "Thunk source file for Guest lib ${NAME} doesn't exist!")
    endif()
  endif()

  add_library(${NAME}-guest ${TARGET_TYPE} ${SOURCE_FILE} ${GEN_lib${NAME}})
  target_include_directories(${NAME}-guest PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/gen/")
  target_compile_definitions(${NAME}-guest PRIVATE GUEST_THUNK_LIBRARY)
  target_link_libraries(${NAME}-guest PRIVATE lib${NAME}-guest-deps)

  target_compile_options(${NAME}-guest PRIVATE -fwrapv)
  if (CMAKE_CURRENT_SOURCE_DIR STREQUAL CMAKE_SOURCE_DIR)
    target_compile_options(${NAME}-guest PRIVATE -msse2 -mfpmath=sse)
  endif()

  if (BITNESS EQUAL 32)
    target_compile_options(${NAME}-guest PRIVATE -fno-plt -fno-stack-protector)
    target_link_options(${NAME}-guest PRIVATE "LINKER:-z,now" "LINKER:-z,relro" "LINKER:-z,notext")
  endif()

  if (BITNESS EQUAL 64 AND EXISTS "${SOURCE_LDS_FILE_ABS}")
    target_link_options(${NAME}-guest PRIVATE "-T" "${CMAKE_CURRENT_SOURCE_DIR}/../lib${NAME}/lib${NAME}_Guest.lds")
    set_property(TARGET ${NAME}-guest APPEND PROPERTY LINK_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/../lib${NAME}/lib${NAME}_Guest.lds")
  endif()

  if (BITNESS EQUAL 32 AND EXISTS "${SOURCE_LDS_32_FILE_ABS}")
    target_link_options(${NAME}-guest PRIVATE "-T" "${CMAKE_CURRENT_SOURCE_DIR}/../lib${NAME}/lib${NAME}_Guest_32.lds")
    set_property(TARGET ${NAME}-guest APPEND PROPERTY LINK_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/../lib${NAME}/lib${NAME}_Guest_32.lds")
  endif()

  target_link_options(${NAME}-guest PRIVATE "LINKER:-soname,${SONAME}")
  set_target_properties(${NAME}-guest PROPERTIES NO_SONAME ON)

  if (GENERATE_GUEST_INSTALL_TARGETS)
    if (BITNESS EQUAL 64)
      install(TARGETS ${NAME}-guest DESTINATION ${DATA_DIRECTORY}/GuestThunks/)
    else()
      install(TARGETS ${NAME}-guest DESTINATION ${DATA_DIRECTORY}/GuestThunks_32/)
    endif()
  endif()
endfunction()

if (BITNESS EQUAL 64)
  generate(libasound ${CMAKE_CURRENT_SOURCE_DIR}/../libasound/libasound_interface.cpp)
  target_include_directories_from_pkgconfig(libasound-guest-deps alsa)
  add_guest_lib(asound "libasound.so.2")

  # Generated split-bridge discriminator. The sidecar owns only one dynamic
  # Vulkan call adapter signature plus the fixed X11 callback unpackers.
  generate(libvulkan_bridge ${CMAKE_CURRENT_SOURCE_DIR}/../libvulkan_bridge/libvulkan_bridge_interface.cpp)
  target_include_directories(libvulkan_bridge-guest-deps INTERFACE ${FEX_PROJECT_SOURCE_DIR}/External/Vulkan-Headers/include/)
  target_include_directories_from_pkgconfig(libvulkan_bridge-guest-deps "x11")
  add_guest_lib(vulkan_bridge "libfex-vulkan-bridge.so")
  set_target_properties(vulkan_bridge-guest PROPERTIES OUTPUT_NAME "fex-vulkan-bridge")
  if (TARGET_TYPE STREQUAL "SHARED")
    target_link_options(vulkan_bridge-guest PRIVATE "LINKER:-z,nodelete")
  endif()

  generate(libvulkan ${CMAKE_CURRENT_SOURCE_DIR}/../libvulkan/libvulkan_interface.cpp)
  target_include_directories(libvulkan-guest-deps INTERFACE ${FEX_PROJECT_SOURCE_DIR}/External/Vulkan-Headers/include/)
  target_include_directories_from_pkgconfig(libvulkan-guest-deps "xcb;x11;xrandr;xrender")
  add_guest_lib(vulkan "libvulkan.so.1")
  target_link_libraries(vulkan-guest PRIVATE vulkan_bridge-guest)

  generate(libdrm ${CMAKE_CURRENT_SOURCE_DIR}/../libdrm/libdrm_interface.cpp)
  target_include_directories_from_pkgconfig(libdrm-guest-deps libdrm /usr/include/drm /usr/include/libdrm)
  add_guest_lib(drm "libdrm.so.2")
endif()

generate(libwayland-client ${CMAKE_CURRENT_SOURCE_DIR}/../libwayland-client/libwayland-client_interface.cpp)
add_guest_lib(wayland-client "libwayland-client.so.0.20.0")
target_include_directories_from_pkgconfig(libwayland-client-guest-deps wayland-client /usr/include/wayland)

generate(libVDSO ${CMAKE_CURRENT_SOURCE_DIR}/../libVDSO/libVDSO_interface.cpp)
add_guest_lib(VDSO "linux-vdso.so.1")
target_compile_options(VDSO-guest PRIVATE "-fno-stack-protector")
target_link_options(VDSO-guest PRIVATE "-nostdlib" "LINKER:--no-undefined" "LINKER:-z,max-page-size=4096" "LINKER:--hash-style=both")

if (BITNESS EQUAL 32)
  target_link_options(VDSO-guest PRIVATE "LINKER:-e,__kernel_vsyscall")
  target_compile_options(VDSO-guest PRIVATE "-fno-pic")
endif()

if (BUILD_FEX_LINUX_TESTS)
  generate(libfex_thunk_test ${CMAKE_CURRENT_SOURCE_DIR}/../libfex_thunk_test/libfex_thunk_test_interface.cpp)
  add_guest_lib(fex_thunk_test "libfex_thunk_test.so")
endif()

generate(libGL ${CMAKE_CURRENT_SOURCE_DIR}/../libGL/libGL_interface.cpp)
target_include_directories_from_pkgconfig(libGL-guest-deps gl)
target_include_directories_from_pkgconfig(libGL-guest-deps "xcb;x11;xrandr;xrender")
add_guest_lib(GL "libGL.so.1")

generate(libEGL ${CMAKE_CURRENT_SOURCE_DIR}/../libEGL/libEGL_interface.cpp)
target_include_directories_from_pkgconfig(libEGL-guest-deps egl)
add_guest_lib(EGL "libEGL.so.1")
target_link_libraries(EGL-guest PRIVATE GL-guest)

add_library(PlaceholderX11 SHARED ../libX11/libX11_NativeGuest.cpp)
target_link_options(PlaceholderX11 PRIVATE "LINKER:-soname,libX11.so.6")
set_target_properties(PlaceholderX11 PROPERTIES NO_SONAME ON)
target_link_libraries(GL-guest PRIVATE PlaceholderX11)

generate(libcuda ${CMAKE_CURRENT_SOURCE_DIR}/../libcuda/libcuda_interface.cpp)
add_guest_lib(cuda "libcuda.so.1")
