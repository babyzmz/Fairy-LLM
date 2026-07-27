#include "fairy_omni/backend.hpp"

namespace fairy::omni {
namespace {

class ContractBackend final : public Backend {
  public:
    [[nodiscard]] BackendStatus status() const override {
        return {false, false, false, "contract_backend"};
    }

    BackendStatus load(const BackendModelPaths &) override {
        return status();
    }

    bool begin() override {
        return true;
    }

    bool submit(MediaBatch) override {
        return true;
    }

    std::optional<BackendDecision> poll(int) override {
        return BackendDecision{true, false, ""};
    }

    void cancel() noexcept override {}

    void stop() noexcept override {}
};

} // namespace

std::unique_ptr<Backend> make_backend() {
    return std::make_unique<ContractBackend>();
}

} // namespace fairy::omni
