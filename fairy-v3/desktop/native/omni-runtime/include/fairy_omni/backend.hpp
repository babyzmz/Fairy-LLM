#pragma once

#include <filesystem>
#include <memory>
#include <string>

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

class Backend {
  public:
    virtual ~Backend() = default;
    [[nodiscard]] virtual BackendStatus status() const = 0;
    virtual BackendStatus load(const BackendModelPaths &paths) = 0;
    virtual void stop() noexcept = 0;
};

std::unique_ptr<Backend> make_backend();

} // namespace fairy::omni
