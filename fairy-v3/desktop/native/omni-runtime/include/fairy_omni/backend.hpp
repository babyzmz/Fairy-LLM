#pragma once

#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "fairy_omni/media.hpp"

namespace fairy::omni {

struct BackendModelPaths {
    std::filesystem::path llm;
    std::filesystem::path vision;
    std::filesystem::path audio;
};

struct BackendStatus {
    bool compiled = false;
    bool cuda = false;
    bool ready = false;
    std::string reason;
};

struct BackendDecision {
    bool ok = false;
    bool speak = false;
    std::string text;
};

class Backend {
  public:
    virtual ~Backend() = default;
    [[nodiscard]] virtual BackendStatus status() const = 0;
    virtual BackendStatus load(const BackendModelPaths &paths) = 0;
    virtual bool begin() = 0;
    virtual bool submit(MediaBatch batch) = 0;
    virtual std::optional<BackendDecision> poll(int timeout_ms) = 0;
    virtual void cancel() noexcept = 0;
    virtual void stop() noexcept = 0;
};

std::unique_ptr<Backend> make_backend();

} // namespace fairy::omni
