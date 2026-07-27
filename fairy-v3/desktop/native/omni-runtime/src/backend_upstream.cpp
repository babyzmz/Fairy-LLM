#include "fairy_omni/backend.hpp"

#include "common.h"
#include "omni.h"

#include <system_error>

#if defined(FAIRY_OMNI_PRODUCTION_CUDA) && !defined(GGML_USE_CUDA)
#error "The production-cuda profile must compile the pinned upstream with CUDA."
#endif

namespace fairy::omni {
namespace {

bool regular_model_file(const std::filesystem::path &path) {
    std::error_code error;
    return !path.empty() && std::filesystem::is_regular_file(path, error) && !error &&
           !std::filesystem::is_symlink(path, error) && !error;
}

class UpstreamBackend final : public Backend {
  public:
    ~UpstreamBackend() override {
        stop();
    }

    [[nodiscard]] BackendStatus status() const override {
#if defined(FAIRY_OMNI_PRODUCTION_CUDA)
        return {true, true, context_ != nullptr,
                context_ != nullptr ? "ready" : "model_not_loaded"};
#else
        return {true, false, false, "cpu_compatibility_only"};
#endif
    }

    BackendStatus load(const BackendModelPaths &paths) override {
        stop();
        if (!regular_model_file(paths.llm) || !regular_model_file(paths.vision) ||
            !regular_model_file(paths.audio)) {
            return {true, cuda_compiled(), false, "model_files_invalid"};
        }

        params_ = common_params{};
        params_.offline = true;
        params_.model.path = paths.llm.string();
        params_.vpm_model = paths.vision.string();
        params_.apm_model = paths.audio.string();
        params_.tts_model.clear();
        params_.tts_bin_dir.clear();
        params_.save_logits = false;
#if defined(FAIRY_OMNI_PRODUCTION_CUDA)
        params_.n_gpu_layers = -1;
        params_.mmproj_use_gpu = true;
#else
        params_.n_gpu_layers = 0;
        params_.mmproj_use_gpu = false;
#endif

        context_ = omni_init(
            &params_,
            2,
            false,
            "",
            -1,
            "gpu:0",
            true,
            nullptr,
            nullptr,
            ""
        );
        if (context_ == nullptr) {
            return {true, cuda_compiled(), false, "upstream_init_failed"};
        }
        context_->async = true;
        if (!omni_set_output_policy(context_, false, false, 8192)) {
            stop();
            return {true, cuda_compiled(), false, "memory_policy_rejected"};
        }
        return status();
    }

    void stop() noexcept override {
        if (context_ != nullptr) {
            omni_free(context_);
            context_ = nullptr;
        }
    }

  private:
    static constexpr bool cuda_compiled() {
#if defined(FAIRY_OMNI_PRODUCTION_CUDA)
        return true;
#else
        return false;
#endif
    }

    common_params params_{};
    omni_context *context_ = nullptr;
};

} // namespace

std::unique_ptr<Backend> make_backend() {
    return std::make_unique<UpstreamBackend>();
}

} // namespace fairy::omni
