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
set(
  FAIRY_OMNI_PATCHED_SOURCE
  "${CMAKE_CURRENT_SOURCE_DIR}/_deps/llama.cpp-omni-patched"
  CACHE PATH
  "Verified and patched llama.cpp-omni source tree"
)

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
  src/backend_contract.cpp
  src/control.cpp
  src/media.cpp
  src/media_pipe_windows.cpp
  src/self_test.cpp
)

if(NOT FAIRY_OMNI_BUILD_PROFILE STREQUAL "contract")
  if(NOT EXISTS "${FAIRY_OMNI_PATCHED_SOURCE}/tools/omni/omni.h")
    message(FATAL_ERROR "Verified patched upstream is missing; run verify-patches.ps1 first")
  endif()

  set(LLAMA_STANDALONE OFF CACHE BOOL "" FORCE)
  set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
  set(LLAMA_BUILD_COMMON ON CACHE BOOL "" FORCE)
  set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
  set(LLAMA_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
  set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
  set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
  set(LLAMA_BUILD_APP OFF CACHE BOOL "" FORCE)
  set(LLAMA_OPENSSL OFF CACHE BOOL "" FORCE)
  set(LLAMA_LLGUIDANCE OFF CACHE BOOL "" FORCE)
  set(GGML_RPC OFF CACHE BOOL "" FORCE)
  if(FAIRY_OMNI_BUILD_PROFILE STREQUAL "production-cuda")
    set(GGML_CUDA ON CACHE BOOL "" FORCE)
  elseif(FAIRY_OMNI_BUILD_PROFILE STREQUAL "upstream-cpu")
    set(GGML_CUDA OFF CACHE BOOL "" FORCE)
  else()
    message(FATAL_ERROR "Unknown Fairy Omni build profile '${FAIRY_OMNI_BUILD_PROFILE}'")
  endif()

  add_subdirectory(
    "${FAIRY_OMNI_PATCHED_SOURCE}"
    "${CMAKE_CURRENT_BINARY_DIR}/upstream"
    EXCLUDE_FROM_ALL
  )
  add_subdirectory(
    "${FAIRY_OMNI_PATCHED_SOURCE}/tools/omni"
    "${CMAKE_CURRENT_BINARY_DIR}/upstream-tools-omni"
    EXCLUDE_FROM_ALL
  )
  if(MSVC)
    target_compile_options(omni PRIVATE /utf-8)
  endif()

  target_sources(fairy_omni_contract PRIVATE src/backend_upstream.cpp)
  set_source_files_properties(src/backend_contract.cpp PROPERTIES HEADER_FILE_ONLY TRUE)
  target_link_libraries(fairy_omni_contract PUBLIC omni)
  target_include_directories(
    fairy_omni_contract
    SYSTEM PRIVATE
      "${FAIRY_OMNI_PATCHED_SOURCE}/common"
      "${FAIRY_OMNI_PATCHED_SOURCE}/tools/omni"
  )
  if(FAIRY_OMNI_BUILD_PROFILE STREQUAL "production-cuda")
    target_compile_definitions(fairy_omni_contract PRIVATE FAIRY_OMNI_PRODUCTION_CUDA=1)
  endif()
endif()
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

  add_executable(fairy-omni-media-tests tests/media_tests.cpp)
  target_link_libraries(fairy-omni-media-tests PRIVATE fairy_omni_contract)
  if(MSVC)
    target_compile_options(fairy-omni-media-tests PRIVATE /W4 /WX /permissive- /utf-8)
  endif()
  add_test(NAME fairy-omni-media-tests COMMAND fairy-omni-media-tests)
endif()
