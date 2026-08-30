file(GLOB_RECURSE TESTS CONFIGURE_DEPENDS *.cpp)

set(LIBS fmt::fmt vixl::vixl Catch2::Catch2WithMain FEXCore_Base JemallocLibs)
foreach(TEST ${TESTS})
  get_filename_component(TEST_NAME ${TEST} NAME_WLE)
  add_executable(FEXCore_Tests_${TEST_NAME} ${TEST})
  if (TEST_NAME STREQUAL "CodeCacheFile")
    # The format parser is deliberately pure. Compile its one implementation
    # directly so this focused test does not pull in the entire JIT library.
    target_sources(FEXCore_Tests_${TEST_NAME} PRIVATE "../../Source/Interface/Core/CodeCacheFile.cpp")
    target_link_libraries(FEXCore_Tests_${TEST_NAME} PRIVATE fmt::fmt Catch2::Catch2WithMain)
    target_include_directories(FEXCore_Tests_${TEST_NAME} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../../include")
  elseif (TEST_NAME STREQUAL "DiskCacheFile")
    # The block-cache layout parser is deliberately pure. Compile its one
    # implementation directly without the JIT, runtime, or Fossilize I/O.
    target_sources(FEXCore_Tests_${TEST_NAME} PRIVATE "../../Source/Interface/Core/DiskCacheFile.cpp")
    target_link_libraries(FEXCore_Tests_${TEST_NAME} PRIVATE Catch2::Catch2WithMain xxHash::xxhash)
    target_include_directories(FEXCore_Tests_${TEST_NAME} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../../include")
  elseif (TEST_NAME STREQUAL "DiskCacheIndexFile")
    # The Fossilize index parser is deliberately pure. Its byte-level recovery
    # tests do not open a database, initialize FEX, or compile guest code.
    target_sources(FEXCore_Tests_${TEST_NAME} PRIVATE "../../Source/Interface/Core/DiskCacheIndexFile.cpp")
    target_link_libraries(FEXCore_Tests_${TEST_NAME} PRIVATE fmt::fmt Catch2::Catch2WithMain)
    target_include_directories(FEXCore_Tests_${TEST_NAME} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../../include")
  else()
    target_link_libraries(FEXCore_Tests_${TEST_NAME} PRIVATE ${LIBS})
  endif()
  if (TEST_NAME STREQUAL "LookupCache" OR TEST_NAME STREQUAL "CodeCacheConfig" OR TEST_NAME STREQUAL "DiskCacheIndexRecovery")
    target_link_libraries(FEXCore_Tests_${TEST_NAME} PRIVATE FEXCore)
  endif()
  target_include_directories(FEXCore_Tests_${TEST_NAME} PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/../../Source/")
  set_target_properties(FEXCore_Tests_${TEST_NAME} PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/FEXCore_Tests")
  catch_discover_tests(FEXCore_Tests_${TEST_NAME} TEST_SUFFIX ".${TEST_NAME}.FEXCore_Tests")
endforeach()

add_custom_target(fexcore_apitests
  WORKING_DIRECTORY "${CMAKE_BINARY_DIR}/"
  USES_TERMINAL
  COMMAND "ctest" "--output-on-failure" "--timeout" "302" ${TEST_JOB_FLAG} "-R" "\.*.FEXCore_Tests$$")
