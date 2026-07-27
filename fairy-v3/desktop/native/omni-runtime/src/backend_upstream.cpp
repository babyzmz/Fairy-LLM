#include "fairy_omni/backend.hpp"

#include "common.h"
#include "omni.h"

#include <algorithm>
#include <limits>
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

void append_u16(std::vector<std::uint8_t> &output, const std::uint16_t value) {
    output.push_back(static_cast<std::uint8_t>(value & 0xffU));
    output.push_back(static_cast<std::uint8_t>((value >> 8U) & 0xffU));
}

void append_u32(std::vector<std::uint8_t> &output, const std::uint32_t value) {
    output.push_back(static_cast<std::uint8_t>(value & 0xffU));
    output.push_back(static_cast<std::uint8_t>((value >> 8U) & 0xffU));
    output.push_back(static_cast<std::uint8_t>((value >> 16U) & 0xffU));
    output.push_back(static_cast<std::uint8_t>((value >> 24U) & 0xffU));
}

std::vector<std::uint8_t> pcm16_wav(std::vector<std::uint8_t> pcm16) {
    if (pcm16.empty() || (pcm16.size() % 2U) != 0U ||
        pcm16.size() > std::numeric_limits<std::uint32_t>::max() - 36U) {
        return {};
    }
    std::vector<std::uint8_t> wav;
    wav.reserve(44U + pcm16.size());
    wav.insert(wav.end(), {'R', 'I', 'F', 'F'});
    append_u32(wav, static_cast<std::uint32_t>(36U + pcm16.size()));
    wav.insert(wav.end(), {'W', 'A', 'V', 'E', 'f', 'm', 't', ' '});
    append_u32(wav, 16U);
    append_u16(wav, 1U);
    append_u16(wav, 1U);
    append_u32(wav, 16000U);
    append_u32(wav, 32000U);
    append_u16(wav, 2U);
    append_u16(wav, 16U);
    wav.insert(wav.end(), {'d', 'a', 't', 'a'});
    append_u32(wav, static_cast<std::uint32_t>(pcm16.size()));
    wav.insert(wav.end(), pcm16.begin(), pcm16.end());
    std::fill(pcm16.begin(), pcm16.end(), std::uint8_t{0});
    return wav;
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

    bool begin() override {
        if (context_ == nullptr || session_active_) {
            return false;
        }
        session_active_ = omni_duplex_session_begin(context_, "", "");
        return session_active_;
    }

    bool submit(MediaBatch batch) override {
        if (!session_active_ || batch.media_sequence == 0U) {
            return false;
        }
        OmniDuplexFrame frame{};
        frame.aud_bytes = pcm16_wav(std::move(batch.microphone_pcm16));
        frame.img_bytes = std::move(batch.jpeg);
        frame.user_seq = static_cast<std::int64_t>(batch.media_sequence);
        return omni_duplex_push_frame(context_, frame) >= 1;
    }

    std::optional<BackendDecision> poll(const int timeout_ms) override {
        if (!session_active_) {
            return std::nullopt;
        }
        OmniDuplexFrameResult result{};
        if (!omni_duplex_wait_next_frame(context_, &result, timeout_ms)) {
            return std::nullopt;
        }
        return BackendDecision{result.ok, result.is_speak, std::move(result.text)};
    }

    void cancel() noexcept override {
        if (context_ != nullptr) {
            static_cast<void>(stop_speek(context_));
        }
    }

    void stop() noexcept override {
        if (context_ != nullptr && session_active_) {
            omni_duplex_session_end(context_);
        }
        session_active_ = false;
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
    bool session_active_ = false;
};

} // namespace

std::unique_ptr<Backend> make_backend() {
    return std::make_unique<UpstreamBackend>();
}

} // namespace fairy::omni
