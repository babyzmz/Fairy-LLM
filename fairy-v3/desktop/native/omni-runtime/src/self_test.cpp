#include "fairy_omni/identity.hpp"

#include "fairy_omni/backend.hpp"
#include "fairy_omni/control.hpp"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <limits>
#include <set>
#include <string>

namespace fairy::omni {
namespace {

using json = nlohmann::json;
constexpr std::uintmax_t kMaxManifestBytes = 1024U * 1024U;

void require_exact_keys(const json &value, const std::set<std::string> &expected) {
    if (!value.is_object()) {
        throw ProtocolError("manifest must be a JSON object");
    }
    std::set<std::string> actual;
    for (auto it = value.begin(); it != value.end(); ++it) {
        actual.insert(it.key());
    }
    if (actual != expected) {
        throw ProtocolError("manifest contains missing or unknown fields");
    }
}

std::string require_string(const json &value, const char *key, const std::size_t length = 0) {
    if (!value.at(key).is_string()) {
        throw ProtocolError(std::string(key) + " must be a string");
    }
    auto result = value.at(key).get<std::string>();
    if (result.empty() || (length != 0 && result.size() != length)) {
        throw ProtocolError(std::string(key) + " is invalid");
    }
    return result;
}

std::string require_hex(const json &value, const char *key, const std::size_t length) {
    const auto result = require_string(value, key, length);
    if (!std::all_of(result.begin(), result.end(), [](const unsigned char character) {
            return std::isxdigit(character) != 0;
        })) {
        throw ProtocolError(std::string(key) + " must be hexadecimal");
    }
    return result;
}

json read_manifest(const std::filesystem::path &manifest_path) {
    std::error_code error;
    if (!std::filesystem::is_regular_file(manifest_path, error) || error ||
        std::filesystem::is_symlink(manifest_path, error) || error) {
        throw ProtocolError("manifest is not a regular non-symlink file");
    }
    const auto size = std::filesystem::file_size(manifest_path, error);
    if (error || size == 0 || size > kMaxManifestBytes) {
        throw ProtocolError("manifest size is outside its allowed bound");
    }

    std::ifstream input(manifest_path, std::ios::binary);
    std::string contents(static_cast<std::size_t>(size), '\0');
    input.read(contents.data(), static_cast<std::streamsize>(contents.size()));
    if (!input || input.gcount() != static_cast<std::streamsize>(contents.size())) {
        throw ProtocolError("manifest could not be read exactly");
    }

    try {
        return parse_strict_json(contents);
    } catch (const ProtocolError &) {
        throw;
    }
}

std::uint64_t require_nonnegative_integer(const json &value, const char *key) {
    const auto &candidate = value.at(key);
    if (candidate.is_number_unsigned()) {
        return candidate.get<std::uint64_t>();
    }
    if (candidate.is_number_integer()) {
        const auto signed_value = candidate.get<std::int64_t>();
        if (signed_value >= 0) {
            return static_cast<std::uint64_t>(signed_value);
        }
    }
    throw ProtocolError(std::string(key) + " must be a non-negative integer");
}

void validate_files(const json &files) {
    if (!files.is_array() || files.size() != 3U) {
        throw ProtocolError("manifest must declare exactly three Beta model files");
    }
    for (const auto &file : files) {
        require_exact_keys(file, {"path", "size", "sha256", "urls"});
        const auto path = require_string(file, "path");
        const auto relative_path = std::filesystem::path(path);
        if (relative_path.is_absolute() || path.find('\\') != std::string::npos) {
            throw ProtocolError("manifest model path is not a safe forward-slash relative path");
        }
        for (const auto &component : relative_path) {
            if (component == ".." || component == ".") {
                throw ProtocolError("manifest model path contains traversal");
            }
        }
        if (require_nonnegative_integer(file, "size") == 0U) {
            throw ProtocolError("manifest model file identity is invalid");
        }
        static_cast<void>(require_hex(file, "sha256", 64));
        const auto &urls = file.at("urls");
        if (!urls.is_array() || urls.empty()) {
            throw ProtocolError("manifest model file must declare a source URL");
        }
        for (const auto &url : urls) {
            if (!url.is_string() || !url.get_ref<const std::string &>().starts_with("https://")) {
                throw ProtocolError("manifest model URL must use HTTPS");
            }
        }
    }
}

} // namespace

ValidatedModelIdentity validate_model_identity(
    const std::filesystem::path &manifest_path,
    const std::filesystem::path &model_root
) {
    if (model_root.empty()) {
        throw ProtocolError("model root is required");
    }

    const auto manifest = read_manifest(manifest_path);
    require_exact_keys(
        manifest,
        {
            "schema_version",
            "manifest_digest",
            "model_id",
            "version",
            "model_revision",
            "runtime_compatibility",
            "license",
            "predicted_peak_vram_mb",
            "upstream_runtime_revision",
            "patch_set_digest",
            "files",
        }
    );

    if (require_nonnegative_integer(manifest, "schema_version") != 1U) {
        throw ProtocolError("manifest numeric identity is invalid");
    }
    validate_files(manifest.at("files"));

    const auto runtime_compatibility = require_string(manifest, "runtime_compatibility");
    const auto upstream_revision = require_hex(manifest, "upstream_runtime_revision", 40);
    const auto patch_digest = require_hex(manifest, "patch_set_digest", 64);
    if (require_string(manifest, "model_id") != "openbmb/minicpm-o-4.5-fairy-beta" ||
        require_string(manifest, "model_revision") !=
            "502eec5b03eaee9d0d2ce17a176e3490103c9a63" ||
        require_string(manifest, "license") != "Apache-2.0") {
        throw ProtocolError("manifest model identity is not the pinned Fairy Beta model");
    }
    if (runtime_compatibility != kRuntimeCompatibility ||
        upstream_revision != kUpstreamRevision || patch_digest != kPatchSetDigest) {
        throw ProtocolError("manifest runtime identity does not match this binary");
    }

    const auto predicted_peak_mb = require_nonnegative_integer(manifest, "predicted_peak_vram_mb");
    if (predicted_peak_mb == 0 ||
        predicted_peak_mb > (std::numeric_limits<std::uint64_t>::max() / (1024U * 1024U))) {
        throw ProtocolError("predicted model peak is invalid");
    }

    return {
        .manifest_digest = require_hex(manifest, "manifest_digest", 64),
        .model_version = require_string(manifest, "version"),
        .predicted_peak_bytes = predicted_peak_mb * 1024U * 1024U,
        .paths = BackendModelPaths{
            model_root / manifest.at("files").at(0).at("path").get<std::string>(),
            model_root / manifest.at("files").at(1).at("path").get<std::string>(),
            model_root / manifest.at("files").at(2).at("path").get<std::string>(),
        },
    };
}

json build_self_test_report(
    const std::filesystem::path &manifest_path,
    const std::filesystem::path &model_root
) {
    const auto model = validate_model_identity(manifest_path, model_root);
    auto backend = make_backend();
    auto backend_status = backend->status();
    if (kBuildProfile == "production-cuda") {
        backend_status = backend->load(model.paths);
    }
    const auto model_probe = kBuildProfile == "contract"
        ? std::string("not_run_contract")
        : backend_status.reason;

    return {
        {"schema_version", 2},
        {"runtime_compatibility", kRuntimeCompatibility},
        {"manifest_digest", model.manifest_digest},
        {"model_version", model.model_version},
        {"predicted_model_peak_bytes", model.predicted_peak_bytes},
        {"upstream_runtime_revision", kUpstreamRevision},
        {"patch_set_digest", kPatchSetDigest},
        {"build_profile", kBuildProfile},
        {"cuda_compiled", backend_status.cuda},
        {"backend_ready", backend_status.ready},
        {"model_probe", model_probe},
    };
}

} // namespace fairy::omni
