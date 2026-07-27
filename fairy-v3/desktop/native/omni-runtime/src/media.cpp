#include "fairy_omni/media.hpp"

#include "fairy_omni/control.hpp"

#include <algorithm>
#include <array>
#include <deque>
#include <limits>
#include <mutex>
#include <utility>

namespace fairy::omni {
namespace {

constexpr std::array<std::uint8_t, 4> kMagic{'F', 'O', 'M', 'I'};
constexpr std::uint64_t kMicCadenceUs = 20'000;

void write_u16_le(std::span<std::uint8_t> output, const std::uint16_t value) {
    output[0] = static_cast<std::uint8_t>(value & 0xFFU);
    output[1] = static_cast<std::uint8_t>((value >> 8U) & 0xFFU);
}

void write_u32_le(std::span<std::uint8_t> output, const std::uint32_t value) {
    for (std::size_t index = 0; index < 4U; ++index) {
        output[index] = static_cast<std::uint8_t>((value >> (index * 8U)) & 0xFFU);
    }
}

void write_u64_le(std::span<std::uint8_t> output, const std::uint64_t value) {
    for (std::size_t index = 0; index < 8U; ++index) {
        output[index] = static_cast<std::uint8_t>((value >> (index * 8U)) & 0xFFU);
    }
}

std::uint16_t read_u16_le(const std::span<const std::uint8_t> input) {
    return static_cast<std::uint16_t>(
        static_cast<std::uint16_t>(input[0]) |
        static_cast<std::uint16_t>(static_cast<std::uint16_t>(input[1]) << 8U)
    );
}

std::uint32_t read_u32_le(const std::span<const std::uint8_t> input) {
    std::uint32_t value = 0;
    for (std::size_t index = 0; index < 4U; ++index) {
        value |= static_cast<std::uint32_t>(input[index]) << (index * 8U);
    }
    return value;
}

std::uint64_t read_u64_le(const std::span<const std::uint8_t> input) {
    std::uint64_t value = 0;
    for (std::size_t index = 0; index < 8U; ++index) {
        value |= static_cast<std::uint64_t>(input[index]) << (index * 8U);
    }
    return value;
}

void validate_kind_and_size(
    const MediaHeader &header,
    const std::uint32_t video_width,
    const std::uint32_t video_height
) {
    switch (header.kind) {
    case MediaKind::microphone_pcm16:
        if (header.payload_length != kMicPcm16PacketBytes) {
            throw ProtocolError("microphone packet must contain exactly 20 ms of PCM16");
        }
        break;
    case MediaKind::application_pcm16:
        if (header.payload_length == 0U ||
            header.payload_length > kMaxApplicationPcm16Bytes ||
            (header.payload_length % 2U) != 0U) {
            throw ProtocolError("application PCM16 packet is outside its allowed bound");
        }
        break;
    case MediaKind::jpeg:
        if (header.payload_length == 0U || header.payload_length > kMaxJpegBytes) {
            throw ProtocolError("JPEG packet is outside its allowed bound");
        }
        break;
    case MediaKind::bgra: {
        if (video_width == 0U || video_height == 0U) {
            throw ProtocolError("BGRA dimensions were not declared by the active context");
        }
        const auto pixels =
            static_cast<std::uint64_t>(video_width) * static_cast<std::uint64_t>(video_height);
        const auto expected = pixels * 4U;
        if (expected == 0U || expected > kMaxBgraBytes ||
            header.payload_length != expected) {
            throw ProtocolError("BGRA packet does not match the bounded context dimensions");
        }
        break;
    }
    default:
        throw ProtocolError("unknown media kind");
    }
}

} // namespace

std::array<std::uint8_t, kMediaHeaderBytes> encode_media_header(const MediaHeader &header) {
    std::array<std::uint8_t, kMediaHeaderBytes> output{};
    std::copy(kMagic.begin(), kMagic.end(), output.begin());
    write_u16_le(std::span(output).subspan<4, 2>(), header.version);
    write_u16_le(
        std::span(output).subspan<6, 2>(),
        static_cast<std::uint16_t>(header.kind)
    );
    write_u64_le(std::span(output).subspan<8, 8>(), header.session_epoch);
    write_u64_le(std::span(output).subspan<16, 8>(), header.sequence);
    write_u64_le(std::span(output).subspan<24, 8>(), header.timestamp_us);
    write_u32_le(std::span(output).subspan<32, 4>(), header.payload_length);
    return output;
}

MediaHeader decode_media_header(const std::span<const std::uint8_t> bytes) {
    if (bytes.size() != kMediaHeaderBytes ||
        !std::equal(kMagic.begin(), kMagic.end(), bytes.begin())) {
        throw ProtocolError("invalid media magic or header length");
    }

    MediaHeader header{
        .version = read_u16_le(bytes.subspan<4, 2>()),
        .kind = static_cast<MediaKind>(read_u16_le(bytes.subspan<6, 2>())),
        .session_epoch = read_u64_le(bytes.subspan<8, 8>()),
        .sequence = read_u64_le(bytes.subspan<16, 8>()),
        .timestamp_us = read_u64_le(bytes.subspan<24, 8>()),
        .payload_length = read_u32_le(bytes.subspan<32, 4>()),
    };
    if (header.version != kMediaProtocolVersion) {
        throw ProtocolError("unsupported media protocol version");
    }
    if (header.kind != MediaKind::microphone_pcm16 &&
        header.kind != MediaKind::application_pcm16 && header.kind != MediaKind::jpeg &&
        header.kind != MediaKind::bgra) {
        throw ProtocolError("unknown media kind");
    }
    return header;
}

class MediaBuffer::Impl final {
  public:
    void validate_locked(const MediaHeader &header) const {
        if (epoch == 0U || header.session_epoch != epoch) {
            throw ProtocolError("stale media epoch");
        }
        if (header.sequence == 0U || header.sequence <= last_sequence) {
            throw ProtocolError("stale media sequence");
        }
        if (header.timestamp_us == 0U || header.timestamp_us < last_timestamp_us) {
            throw ProtocolError("stale media timestamp");
        }
        if (header.kind == MediaKind::microphone_pcm16 && last_mic_timestamp_us != 0U &&
            (last_mic_timestamp_us > std::numeric_limits<std::uint64_t>::max() -
                                         kMicCadenceUs ||
             header.timestamp_us != last_mic_timestamp_us + kMicCadenceUs)) {
            throw ProtocolError("microphone packet cadence is not exactly 20 ms");
        }
        validate_kind_and_size(header, video_width, video_height);
    }

    void clear_locked() {
        microphone.clear();
        application_audio.clear();
        latest_video.reset();
        microphone_bytes = 0;
        application_bytes = 0;
        video_bytes = 0;
        last_sequence = 0;
        last_timestamp_us = 0;
        last_mic_timestamp_us = 0;
    }

    void trim_ring(std::deque<MediaFrame> &ring, std::size_t &ring_bytes) {
        while (ring_bytes > kMaxAudioRingBytes && !ring.empty()) {
            ring_bytes -= ring.front().payload.size();
            ring.pop_front();
        }
    }

    mutable std::mutex mutex;
    std::uint64_t epoch = 0;
    std::uint64_t last_sequence = 0;
    std::uint64_t last_timestamp_us = 0;
    std::uint64_t last_mic_timestamp_us = 0;
    std::uint32_t video_width = 0;
    std::uint32_t video_height = 0;
    std::deque<MediaFrame> microphone;
    std::deque<MediaFrame> application_audio;
    std::optional<MediaFrame> latest_video;
    std::size_t microphone_bytes = 0;
    std::size_t application_bytes = 0;
    std::size_t video_bytes = 0;
};

MediaBuffer::MediaBuffer() : impl_(std::make_unique<Impl>()) {}

MediaBuffer::~MediaBuffer() = default;

void MediaBuffer::rotate_epoch(const std::uint64_t session_epoch) {
    if (session_epoch == 0U) {
        throw ProtocolError("media epoch must be positive");
    }
    const std::scoped_lock lock(impl_->mutex);
    impl_->clear_locked();
    impl_->epoch = session_epoch;
}

void MediaBuffer::configure_video(const std::uint32_t width, const std::uint32_t height) {
    if ((width == 0U) != (height == 0U)) {
        throw ProtocolError("video dimensions must both be zero or both be positive");
    }
    const auto bytes = static_cast<std::uint64_t>(width) *
                       static_cast<std::uint64_t>(height) * 4U;
    if (bytes > kMaxBgraBytes) {
        throw ProtocolError("video dimensions exceed the BGRA buffer bound");
    }
    const std::scoped_lock lock(impl_->mutex);
    impl_->video_width = width;
    impl_->video_height = height;
    if (impl_->latest_video.has_value() &&
        impl_->latest_video->header.kind == MediaKind::bgra &&
        impl_->latest_video->payload.size() != bytes) {
        impl_->latest_video.reset();
        impl_->video_bytes = 0;
    }
}

void MediaBuffer::validate_before_allocation(const MediaHeader &header) const {
    const std::scoped_lock lock(impl_->mutex);
    impl_->validate_locked(header);
}

void MediaBuffer::commit(MediaHeader header, std::vector<std::uint8_t> payload) {
    const std::scoped_lock lock(impl_->mutex);
    impl_->validate_locked(header);
    if (payload.size() != header.payload_length) {
        throw ProtocolError("media payload length changed after validation");
    }

    impl_->last_sequence = header.sequence;
    impl_->last_timestamp_us = header.timestamp_us;
    if (header.kind == MediaKind::microphone_pcm16) {
        impl_->last_mic_timestamp_us = header.timestamp_us;
        impl_->microphone_bytes += payload.size();
        impl_->microphone.push_back(MediaFrame{header, std::move(payload)});
        impl_->trim_ring(impl_->microphone, impl_->microphone_bytes);
    } else if (header.kind == MediaKind::application_pcm16) {
        impl_->application_bytes += payload.size();
        impl_->application_audio.push_back(MediaFrame{header, std::move(payload)});
        impl_->trim_ring(impl_->application_audio, impl_->application_bytes);
    } else {
        impl_->latest_video = MediaFrame{header, std::move(payload)};
        impl_->video_bytes = impl_->latest_video->payload.size();
    }

    if (impl_->microphone_bytes + impl_->application_bytes + impl_->video_bytes >
        kMaxMediaBufferBytes) {
        throw ProtocolError("aggregate media buffer exceeded its hard bound");
    }
}

void MediaBuffer::clear() {
    const std::scoped_lock lock(impl_->mutex);
    impl_->clear_locked();
    impl_->epoch = 0;
    impl_->video_width = 0;
    impl_->video_height = 0;
}

MediaBufferStats MediaBuffer::stats() const {
    const std::scoped_lock lock(impl_->mutex);
    return {
        .microphone_frames = impl_->microphone.size(),
        .application_audio_frames = impl_->application_audio.size(),
        .has_video_frame = impl_->latest_video.has_value(),
        .buffered_bytes =
            impl_->microphone_bytes + impl_->application_bytes + impl_->video_bytes,
        .last_sequence = impl_->last_sequence,
    };
}

} // namespace fairy::omni
