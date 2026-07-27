#pragma once

#include <cstddef>
#include <cstdint>
#include <istream>
#include <ostream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include <nlohmann/json.hpp>

namespace fairy::omni {

inline constexpr std::size_t kMaxControlPayloadBytes = 256U * 1024U;
inline constexpr std::uint64_t kControlProtocolVersion = 1;

class ProtocolError final : public std::runtime_error {
  public:
    using std::runtime_error::runtime_error;
};

enum class FrameReadStatus {
    frame,
    eof,
};

struct FrameReadResult {
    FrameReadStatus status;
    nlohmann::json payload;
};

std::vector<std::uint8_t> encode_frame(const nlohmann::json &payload);
nlohmann::json parse_strict_json(std::string_view input);
FrameReadResult read_frame(std::istream &input);
void write_frame(std::ostream &output, const nlohmann::json &payload);

class ControlRuntime final {
  public:
    std::vector<nlohmann::json> handle(const nlohmann::json &command);
    [[nodiscard]] bool stopped() const noexcept;

  private:
    std::string session_id_;
    std::string segment_id_;
    std::uint64_t context_epoch_ = 0;
    std::uint64_t last_sequence_ = 0;
    std::uint64_t last_media_sequence_ = 0;
    bool initialized_ = false;
    bool loaded_ = false;
    bool context_active_ = false;
    bool stopped_ = false;
};

} // namespace fairy::omni
