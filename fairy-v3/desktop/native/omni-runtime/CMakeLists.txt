cmake_minimum_required(VERSION 3.25)

project(fairy_omni_runtime LANGUAGES CXX)

include(CTest)

set(FAIRY_OMNI_BUILD_PROFILE "contract" CACHE STRING "Fairy Omni build profile")
set_property(CACHE FAIRY_OMNI_BUILD_PROFILE PROPERTY STRINGS contract upstream-cpu production-cuda)
set(
  FAIRY_OMNI_UPSTREAM_SOURCE
  "${CMAKE_CURRENT_SOURCE_DIR}/_deps/llama.cpp-omni-source"
  CACHE PATH
  "Pinned llama.cpp-omni source tree"
)

if(NOT FAIRY_OMNI_BUILD_PROFILE STREQUAL "contract")
  message(FATAL_ERROR "Only the fail-closed contract profile is available before the upstream adapter task")
endif()

file(READ "${CMAKE_CURRENT_SOURCE_DIR}/upstream.lock.json" FAIRY_OMNI_LOCK_JSON)
string(JSON FAIRY_OMNI_UPSTREAM_REVISION GET "${FAIRY_OMNI_LOCK_JSON}" upstream revision)
string(JSON FAIRY_OMNI_PATCH_SET_DIGEST GET "${FAIRY_OMNI_LOCK_JSON}" patch_set digest)
string(JSON FAIRY_OMNI_RUNTIME_COMPATIBILITY GET "${FAIRY_OMNI_LOCK_JSON}" runtime_compatibility)

set(FAIRY_NLOHMANN_INCLUDE "${FAIRY_OMNI_UPSTREAM_SOURCE}/vendor")
if(NOT EXISTS "${FAIRY_NLOHMANN_INCLUDE}/nlohmann/json.hpp")
  message(FATAL_ERROR "Pinned nlohmann JSON header is missing; run sync-upstream.ps1 first")
endif()

add_library(
  fairy_omni_contract
  STATIC
  src/control.cpp
  src/self_test.cpp
)
target_compile_features(fairy_omni_contract PUBLIC cxx_std_20)
target_include_directories(
  fairy_omni_contract
  PUBLIC
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${FAIRY_NLOHMANN_INCLUDE}"
)
target_compile_definitions(
  fairy_omni_contract
  PUBLIC
    FAIRY_OMNI_BUILD_PROFILE="${FAIRY_OMNI_BUILD_PROFILE}"
    FAIRY_OMNI_RUNTIME_COMPATIBILITY="${FAIRY_OMNI_RUNTIME_COMPATIBILITY}"
    FAIRY_OMNI_UPSTREAM_REVISION="${FAIRY_OMNI_UPSTREAM_REVISION}"
    FAIRY_OMNI_PATCH_SET_DIGEST="${FAIRY_OMNI_PATCH_SET_DIGEST}"
)

if(MSVC)
  target_compile_options(fairy_omni_contract PRIVATE /W4 /WX /permissive- /utf-8)
else()
  target_compile_options(fairy_omni_contract PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()

add_executable(fairy-omni-runtime src/main.cpp)
target_link_libraries(fairy-omni-runtime PRIVATE fairy_omni_contract)
if(MSVC)
  target_compile_options(fairy-omni-runtime PRIVATE /W4 /WX /permissive- /utf-8)
endif()

if(BUILD_TESTING)
  add_executable(fairy-omni-control-tests tests/control_tests.cpp)
  target_link_libraries(fairy-omni-control-tests PRIVATE fairy_omni_contract)
  target_compile_definitions(
    fairy-omni-control-tests
    PRIVATE
      FAIRY_OMNI_TEST_MANIFEST="${CMAKE_CURRENT_SOURCE_DIR}/../../src-tauri/resources/omni/minicpm-o-4.5.json"
  )
  if(MSVC)
    target_compile_options(fairy-omni-control-tests PRIVATE /W4 /WX /permissive- /utf-8)
  endif()
  add_test(NAME fairy-omni-control-tests COMMAND fairy-omni-control-tests)
endif()
