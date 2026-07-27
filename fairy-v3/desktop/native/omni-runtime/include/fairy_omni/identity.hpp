#pragma once

#include <filesystem>
#include <string_view>

#include <nlohmann/json.hpp>

namespace fairy::omni {

inline constexpr std::string_view kBuildProfile = FAIRY_OMNI_BUILD_PROFILE;
inline constexpr std::string_view kRuntimeCompatibility = FAIRY_OMNI_RUNTIME_COMPATIBILITY;
inline constexpr std::string_view kUpstreamRevision = FAIRY_OMNI_UPSTREAM_REVISION;
inline constexpr std::string_view kPatchSetDigest = FAIRY_OMNI_PATCH_SET_DIGEST;

nlohmann::json build_self_test_report(
    const std::filesystem::path &manifest_path,
    const std::filesystem::path &model_root
);

} // namespace fairy::omni
