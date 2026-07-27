#pragma once

#include <filesystem>
#include <string>
#include <string_view>

#include <nlohmann/json.hpp>

#include "fairy_omni/backend.hpp"

namespace fairy::omni {

inline constexpr std::string_view kBuildProfile = FAIRY_OMNI_BUILD_PROFILE;
inline constexpr std::string_view kRuntimeCompatibility = FAIRY_OMNI_RUNTIME_COMPATIBILITY;
inline constexpr std::string_view kUpstreamRevision = FAIRY_OMNI_UPSTREAM_REVISION;
inline constexpr std::string_view kPatchSetDigest = FAIRY_OMNI_PATCH_SET_DIGEST;

struct ValidatedModelIdentity {
    std::string manifest_digest;
    std::string model_version;
    std::uint64_t predicted_peak_bytes = 0;
    BackendModelPaths paths;
};

ValidatedModelIdentity validate_model_identity(
    const std::filesystem::path &manifest_path,
    const std::filesystem::path &model_root
);

nlohmann::json build_self_test_report(
    const std::filesystem::path &manifest_path,
    const std::filesystem::path &model_root
);

} // namespace fairy::omni
