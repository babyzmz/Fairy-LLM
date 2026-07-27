#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <vector>

namespace fairy::omni {

inline constexpr std::size_t kMediaHeaderBytes = 36;
inline constexpr std::uint16_t kMediaProtocolVersion = 1;
inline constexpr std::size_t kMicPcm16PacketBytes = 640;
inline constexpr std::size_t kMaxApplicationPcm16Bytes = 32000;
inline constexpr std::size_t kMaxJpegBytes = 8U * 1024U * 1024U;
inline constexpr std::size_t kMaxBgraBytes = 32U * 1024U * 1024U;
inline constexpr std::size_t kMaxMediaBufferBytes = 48U * 1024U * 1024U;
inline constexpr std::size_t kMaxAudioRingBytes = 8U * 1024U * 1024U;

enum class MediaKind : std::uint16_t {
    microphone_pcm16 = 1,
    application_pcm16 = 2,
    jpeg = 3,
    bgra = 4,
};

struct MediaHeader {
    std::uint16_t version = 0;
    MediaKind kind = MediaKind::microphone_pcm16;
    std::uint64_t session_epoch = 0;
    std::uint64_t sequence = 0;
    std::uint64_t timestamp_us = 0;
    std::uint32_t payload_length = 0;
};

struct MediaFrame {
    MediaHeader header;
    std::vector<std::uint8_t> payload;
};

struct MediaBufferStats {
    std::size_t microphone_frames = 0;
    std::size_t application_audio_frames = 0;
    bool has_video_frame = false;
    std::size_t buffered_bytes = 0;
    std::uint64_t last_sequence = 0;
};

struct MediaBatch {
    std::vector<std::uint8_t> microphone_pcm16;
    std::vector<std::uint8_t> jpeg;
    std::uint64_t media_sequence = 0;
};

std::array<std::uint8_t, kMediaHeaderBytes> encode_media_header(const MediaHeader &header);
MediaHeader decode_media_header(std::span<const std::uint8_t> bytes);

class MediaBuffer final {
  public:
    MediaBuffer();
    ~MediaBuffer();
    MediaBuffer(const MediaBuffer &) = delete;
    MediaBuffer &operator=(const MediaBuffer &) = delete;

    void rotate_epoch(std::uint64_t session_epoch);
    void configure_video(std::uint32_t width, std::uint32_t height);
    void validate_before_allocation(const MediaHeader &header) const;
    void commit(MediaHeader header, std::vector<std::uint8_t> payload);
    MediaBatch take_batch();
    void clear();
    [[nodiscard]] MediaBufferStats stats() const;

  private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

class MediaPipePump final {
  public:
    MediaPipePump(std::filesystem::path pipe_name, MediaBuffer &buffer);
    ~MediaPipePump();
    MediaPipePump(const MediaPipePump &) = delete;
    MediaPipePump &operator=(const MediaPipePump &) = delete;

    void start();
    void stop() noexcept;
    [[nodiscard]] std::optional<std::string> take_error();

  private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace fairy::omni
