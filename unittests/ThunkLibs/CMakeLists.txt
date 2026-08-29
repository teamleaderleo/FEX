add_executable(thunkgentest generator.cpp abi.cpp)
target_link_libraries(thunkgentest PRIVATE Catch2::Catch2WithMain)
target_link_libraries(thunkgentest PRIVATE fmt::fmt)
target_link_libraries(thunkgentest PRIVATE thunkgenlib)
catch_discover_tests(thunkgentest TEST_SUFFIX ".ThunkGen")

target_compile_definitions(thunkgentest PRIVATE
  "X86_DEV_ROOTFS=\"${X86_DEV_ROOTFS}\""
)

add_test(NAME VulkanCustomRouteInventory.ThunkGen
  COMMAND python3 "${CMAKE_CURRENT_SOURCE_DIR}/vulkan_custom_route_inventory.py" "${CMAKE_SOURCE_DIR}")

add_test(NAME VulkanInstancePNextCopyInventory.ThunkGen
  COMMAND python3 "${CMAKE_CURRENT_SOURCE_DIR}/vulkan_instance_pnext_copy_inventory.py" "${CMAKE_SOURCE_DIR}")

add_test(NAME VulkanRepackCleanupInventory.ThunkGen
  COMMAND python3 "${CMAKE_CURRENT_SOURCE_DIR}/vulkan_repack_cleanup_inventory.py" "${CMAKE_SOURCE_DIR}")

add_custom_target(thunkgen_tests
  WORKING_DIRECTORY "${CMAKE_BINARY_DIR}"
  USES_TERMINAL
  COMMAND "ctest" "--output-on-failure" "--timeout" "302" ${TEST_JOB_FLAG} "-R" "\.*.ThunkGen")
add_dependencies(thunkgen_tests thunkgentest)
