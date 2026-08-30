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
  elseif (TEST_NAME STREQUAL "DiskCacheIndexRecovery")
    # Compile the real Fossilize storage owner without pulling in the JIT and
    # runtime object graph. This remains the file/lock/recovery acceptance test.
    target_sources(FEXCore_Tests_${TEST_NAME} PRIVATE
      "../../Source/Interface/Core/DiskCacheStorage.cpp"
      "../../Source/Interface/Core/DiskCacheFile.cpp"
      "../../Source/Interface/Core/DiskCacheIndexFile.cpp"
      "../../Source/Utils/AllocatorHooks.cpp"
      "../../Source/Utils/ForcedAssert.cpp"
      "../../Source/Utils/LogManager.cpp")
    target_link_libraries(FEXCore_Tests_${TEST_NAME} PRIVATE
      fmt::fmt Catch2::Catch2WithMain unordered_dense::unordered_dense xxHash::xxhash)
    target_include_directories(FEXCore_Tests_${TEST_NAME} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../../include")
  elseif (TEST_NAME STREQUAL "CodeCacheConfig")
    # Compile the production code-cache identity owner without pulling in the
    # runtime/JIT object graph.
    target_sources(FEXCore_Tests_${TEST_NAME} PRIVATE
      "../../Source/Interface/Core/CodeCacheConfig.cpp"
      "../../Source/Utils/AllocatorHooks.cpp")
    target_link_libraries(FEXCore_Tests_${TEST_NAME} PRIVATE FEXCore_Base Catch2::Catch2WithMain)
  else()
    target_link_libraries(FEXCore_Tests_${TEST_NAME} PRIVATE ${LIBS})
  endif()
  if (TEST_NAME STREQUAL "LookupCache")
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
