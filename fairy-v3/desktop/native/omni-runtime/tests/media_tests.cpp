#include "fairy_omni/control.hpp"
#include "fairy_omni/media.hpp"

#include <array>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using fairy::omni::MediaBuffer;
using fairy::omni::MediaHeader;
using fairy::omni::MediaKind;
using fairy::omni::ProtocolError;

void require(const bool condition, const std::string &message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

void require_protocol_error(const std::function<void()> &action, const std::string &name) {
    try {
        action();
    } catch (const ProtocolError &) {
        return;
    }
    throw std::runtime_error(name + " did not fail closed");
}

MediaHeader mic_header(
    const std::uint64_t sequence,
    const std::uint64_t timestamp,
    const std::uint64_t epoch = 7
) {
    return {
        .version = fairy::omni::kMediaProtocolVersion,
        .kind = MediaKind::microphone_pcm16,
        .session_epoch = epoch,
        .sequence = sequence,
        .timestamp_us = timestamp,
        .payload_length = fairy::omni::kMicPcm16PacketBytes,
    };
}

void test_header_roundtrip() {
    const auto original = mic_header(9, 180000);
    const auto encoded = fairy::omni::encode_media_header(original);
    const auto decoded = fairy::omni::decode_media_header(encoded);
    require(decoded.version == original.version, "media version changed");
    require(decoded.kind == original.kind, "media kind changed");
    require(decoded.session_epoch == original.session_epoch, "media epoch changed");
    require(decoded.sequence == original.sequence, "media sequence changed");
    require(decoded.timestamp_us == original.timestamp_us, "media timestamp changed");
    require(decoded.payload_length == original.payload_length, "media length changed");

    auto bad_magic = encoded;
    bad_magic[0] = 'X';
    require_protocol_error(
        [&] { static_cast<void>(fairy::omni::decode_media_header(bad_magic)); },
        "bad magic"
    );
    auto bad_kind = encoded;
    bad_kind[6] = 9;
    require_protocol_error(
        [&] { static_cast<void>(fairy::omni::decode_media_header(bad_kind)); },
        "bad kind"
    );
}

void test_bounds_and_sequences() {
    MediaBuffer buffer;
    buffer.rotate_epoch(7);
    buffer.configure_video(640, 360);

    const auto first = mic_header(1, 20000);
    buffer.validate_before_allocation(first);
    buffer.commit(first, std::vector<std::uint8_t>(first.payload_length));
    const auto second = mic_header(2, 40000);
    buffer.commit(second, std::vector<std::uint8_t>(second.payload_length));
    require(buffer.stats().microphone_frames == 2U, "microphone frames were not retained");

    require_protocol_error(
        [&] {
            const auto stale = mic_header(2, 60000);
            buffer.validate_before_allocation(stale);
        },
        "stale sequence"
    );
    require_protocol_error(
        [&] {
            const auto stale_epoch = mic_header(3, 60000, 6);
            buffer.validate_before_allocation(stale_epoch);
        },
        "stale epoch"
    );
    require_protocol_error(
        [&] {
            const auto bad_cadence = mic_header(3, 61000);
            buffer.validate_before_allocation(bad_cadence);
        },
        "bad microphone cadence"
    );
    require_protocol_error(
        [&] {
            auto oversized = MediaHeader{
                .version = 1,
                .kind = MediaKind::jpeg,
                .session_epoch = 7,
                .sequence = 3,
                .timestamp_us = 60000,
                .payload_length = static_cast<std::uint32_t>(fairy::omni::kMaxJpegBytes + 1U),
            };
            buffer.validate_before_allocation(oversized);
        },
        "oversized JPEG before allocation"
    );

    auto bgra = MediaHeader{
        .version = 1,
        .kind = MediaKind::bgra,
        .session_epoch = 7,
        .sequence = 3,
        .timestamp_us = 60000,
        .payload_length = 640U * 360U * 4U,
    };
    buffer.commit(bgra, std::vector<std::uint8_t>(bgra.payload_length));
    require(buffer.stats().has_video_frame, "video frame was not retained");

    auto jpeg = MediaHeader{
        .version = 1,
        .kind = MediaKind::jpeg,
        .session_epoch = 7,
        .sequence = 4,
        .timestamp_us = 60000,
        .payload_length = 1024,
    };
    buffer.commit(jpeg, std::vector<std::uint8_t>(jpeg.payload_length));
    const auto stats = buffer.stats();
    require(stats.has_video_frame, "latest video slot was cleared");
    require(
        stats.buffered_bytes == (2U * fairy::omni::kMicPcm16PacketBytes) + 1024U,
        "latest video replacement did not release the prior frame"
    );

    buffer.rotate_epoch(8);
    const auto rotated = buffer.stats();
    require(rotated.buffered_bytes == 0U && rotated.last_sequence == 0U, "rotation leaked media");
}

void test_audio_ring_bound() {
    MediaBuffer buffer;
    buffer.rotate_epoch(1);
    const auto frame_count =
        (fairy::omni::kMaxAudioRingBytes / fairy::omni::kMicPcm16PacketBytes) + 4U;
    for (std::size_t index = 0; index < frame_count; ++index) {
        const auto sequence = static_cast<std::uint64_t>(index + 1U);
        const auto header = mic_header(sequence, sequence * 20000U, 1);
        buffer.commit(header, std::vector<std::uint8_t>(header.payload_length));
    }
    const auto stats = buffer.stats();
    require(
        stats.buffered_bytes <= fairy::omni::kMaxAudioRingBytes,
        "microphone ring exceeded its bound"
    );
    require(stats.last_sequence == frame_count, "ring trimming changed sequence state");
}

} // namespace

int main() {
    try {
        test_header_roundtrip();
        test_bounds_and_sequences();
        test_audio_ring_bound();
        std::cout << "media tests passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception &error) {
        std::cerr << "media tests failed: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
