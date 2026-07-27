#include "fairy_omni/control.hpp"

#include "fairy_omni/identity.hpp"
#include "fairy_omni/media.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <limits>
#include <set>
#include <sstream>
#include <string_view>
#include <vector>

namespace fairy::omni {
namespace {

using json = nlohmann::json;

constexpr std::size_t kMaxIdentityBytes = 128;
constexpr std::size_t kMaxNonceBytes = 128;
constexpr std::size_t kMaxSummaryBytes = 4096;

const std::set<std::string> kCommonKeys{
    "type",
    "session_id",
    "segment_id",
    "context_epoch",
    "sequence",
};

void require_exact_keys(const json &value, std::set<std::string> expected) {
    if (!value.is_object()) {
        throw ProtocolError("control payload must be a JSON object");
    }

    std::set<std::string> actual;
    for (auto it = value.begin(); it != value.end(); ++it) {
        actual.insert(it.key());
    }
    if (actual != expected) {
        throw ProtocolError("control payload contains missing or unknown fields");
    }
}

std::set<std::string> keys_with(std::initializer_list<std::string_view> extra) {
    auto keys = kCommonKeys;
    for (const auto key : extra) {
        keys.emplace(key);
    }
    return keys;
}

std::string require_string(
    const json &value,
    std::string_view key,
    const std::size_t max_bytes,
    const bool allow_empty = false
) {
    const auto key_string = std::string(key);
    if (!value.at(key_string).is_string()) {
        throw ProtocolError(key_string + " must be a string");
    }
    auto result = value.at(key_string).get<std::string>();
    if ((!allow_empty && result.empty()) || result.size() > max_bytes ||
        result.find('\0') != std::string::npos) {
        throw ProtocolError(key_string + " is outside its allowed bound");
    }
    return result;
}

void require_safe_identifier(const std::string &value, std::string_view key) {
    const auto safe = std::all_of(value.begin(), value.end(), [](const unsigned char character) {
        return std::isalnum(character) != 0 || character == '-' || character == '_' ||
               character == ':' || character == '.';
    });
    if (!safe) {
        throw ProtocolError(std::string(key) + " contains an unsupported character");
    }
}

void require_sha256_hex(const std::string &value, std::string_view key) {
    const auto hex = value.size() == 64U &&
                     std::all_of(value.begin(), value.end(), [](const unsigned char character) {
                         return std::isxdigit(character) != 0;
                     });
    if (!hex) {
        throw ProtocolError(std::string(key) + " must be a SHA-256 hex digest");
    }
}

std::uint64_t require_positive_u64(const json &value, std::string_view key) {
    const auto key_string = std::string(key);
    const auto &candidate = value.at(key_string);
    if (candidate.is_number_unsigned()) {
        const auto result = candidate.get<std::uint64_t>();
        if (result == 0) {
            throw ProtocolError(key_string + " must be positive");
        }
        return result;
    }
    if (!candidate.is_number_integer()) {
        throw ProtocolError(key_string + " must be an unsigned integer");
    }
    const auto signed_result = candidate.get<std::int64_t>();
    if (signed_result <= 0) {
        throw ProtocolError(key_string + " must be positive");
    }
    return static_cast<std::uint64_t>(signed_result);
}

std::uint64_t require_bounded_u64(
    const json &value,
    std::string_view key,
    const std::uint64_t maximum
) {
    const auto result = require_positive_u64(value, key);
    if (result > maximum) {
        throw ProtocolError(std::string(key) + " exceeds its allowed bound");
    }
    return result;
}

std::uint64_t require_bounded_nonnegative_u64(
    const json &value,
    std::string_view key,
    const std::uint64_t maximum
) {
    const auto key_string = std::string(key);
    const auto &candidate = value.at(key_string);
    std::uint64_t result = 0;
    if (candidate.is_number_unsigned()) {
        result = candidate.get<std::uint64_t>();
    } else if (candidate.is_number_integer()) {
        const auto signed_result = candidate.get<std::int64_t>();
        if (signed_result < 0) {
            throw ProtocolError(key_string + " must be non-negative");
        }
        result = static_cast<std::uint64_t>(signed_result);
    } else {
        throw ProtocolError(key_string + " must be an unsigned integer");
    }
    if (result > maximum) {
        throw ProtocolError(key_string + " exceeds its allowed bound");
    }
    return result;
}

std::string require_type(const json &command) {
    if (!command.is_object() || !command.contains("type")) {
        throw ProtocolError("control payload is missing type");
    }
    return require_string(command, "type", 64);
}

json base_event(
    std::string_view type,
    const std::string &session_id,
    const std::string &segment_id,
    const std::uint64_t context_epoch,
    const std::uint64_t sequence
) {
    return {
        {"type", type},
        {"session_id", session_id},
        {"segment_id", segment_id},
        {"context_epoch", context_epoch},
        {"sequence", sequence},
    };
}

void append_u32_le(std::vector<std::uint8_t> &output, const std::uint32_t value) {
    output.push_back(static_cast<std::uint8_t>(value & 0xFFU));
    output.push_back(static_cast<std::uint8_t>((value >> 8U) & 0xFFU));
    output.push_back(static_cast<std::uint8_t>((value >> 16U) & 0xFFU));
    output.push_back(static_cast<std::uint8_t>((value >> 24U) & 0xFFU));
}

std::uint32_t decode_u32_le(const std::array<std::uint8_t, 4> &bytes) {
    return static_cast<std::uint32_t>(bytes[0]) |
           (static_cast<std::uint32_t>(bytes[1]) << 8U) |
           (static_cast<std::uint32_t>(bytes[2]) << 16U) |
           (static_cast<std::uint32_t>(bytes[3]) << 24U);
}

} // namespace

std::vector<std::uint8_t> encode_frame(const json &payload) {
    const auto body = payload.dump();
    if (body.empty() || body.size() > kMaxControlPayloadBytes ||
        body.size() > std::numeric_limits<std::uint32_t>::max()) {
        throw ProtocolError("control frame length is outside its allowed bound");
    }

    std::vector<std::uint8_t> frame;
    frame.reserve(4U + body.size());
    append_u32_le(frame, static_cast<std::uint32_t>(body.size()));
    frame.insert(frame.end(), body.begin(), body.end());
    return frame;
}

nlohmann::json parse_strict_json(const std::string_view input) {
    bool duplicate_key = false;
    std::vector<std::set<std::string>> object_keys;
    const auto callback = [&duplicate_key, &object_keys](
                              const int,
                              const json::parse_event_t event,
                              json &parsed
                          ) {
        if (event == json::parse_event_t::object_start) {
            object_keys.emplace_back();
        } else if (event == json::parse_event_t::key) {
            if (object_keys.empty() ||
                !object_keys.back().insert(parsed.get<std::string>()).second) {
                duplicate_key = true;
            }
        } else if (event == json::parse_event_t::object_end && !object_keys.empty()) {
            object_keys.pop_back();
        }
        return true;
    };

    try {
        auto payload = json::parse(input, callback, true, false);
        if (duplicate_key) {
            throw ProtocolError("duplicate JSON object key");
        }
        return payload;
    } catch (const ProtocolError &) {
        throw;
    } catch (const json::exception &) {
        throw ProtocolError("invalid strict UTF-8 JSON payload");
    }
}

FrameReadResult read_frame(std::istream &input) {
    std::array<std::uint8_t, 4> prefix{};
    input.read(reinterpret_cast<char *>(prefix.data()), static_cast<std::streamsize>(prefix.size()));
    const auto prefix_bytes = input.gcount();
    if (prefix_bytes == 0 && input.eof()) {
        return {FrameReadStatus::eof, json{}};
    }
    if (prefix_bytes != static_cast<std::streamsize>(prefix.size())) {
        throw ProtocolError("truncated control frame prefix");
    }

    const auto length = decode_u32_le(prefix);
    if (length == 0 || length > kMaxControlPayloadBytes) {
        throw ProtocolError("control frame length is outside its allowed bound");
    }

    std::string body(length, '\0');
    input.read(body.data(), static_cast<std::streamsize>(body.size()));
    if (input.gcount() != static_cast<std::streamsize>(body.size())) {
        throw ProtocolError("truncated control frame payload");
    }

    try {
        auto payload = parse_strict_json(body);
        if (!payload.is_object()) {
            throw ProtocolError("control payload must be a JSON object");
        }
        return {FrameReadStatus::frame, std::move(payload)};
    } catch (const ProtocolError &) {
        throw;
    } catch (const json::exception &) {
        throw ProtocolError("invalid strict UTF-8 JSON control payload");
    }
}

void write_frame(std::ostream &output, const json &payload) {
    const auto frame = encode_frame(payload);
    output.write(
        reinterpret_cast<const char *>(frame.data()),
        static_cast<std::streamsize>(frame.size())
    );
    output.flush();
    if (!output) {
        throw ProtocolError("failed to write control frame");
    }
}

std::vector<json> ControlRuntime::handle(const json &command) {
    if (stopped_) {
        throw ProtocolError("runtime is already stopped");
    }

    const auto type = require_type(command);
    std::set<std::string> expected_keys;
    if (type == "hello") {
        expected_keys = keys_with({"protocol_version"});
    } else if (type == "load") {
        expected_keys = keys_with({"manifest_digest", "model_version"});
    } else if (type == "context_begin") {
        expected_keys = keys_with(
            {"context_kind",
             "token_budget",
             "audio_budget_ms",
             "frame_budget",
             "video_width",
             "video_height"}
        );
    } else if (type == "context_rotate") {
        expected_keys = keys_with({"next_context_epoch", "reason", "public_summary"});
    } else if (type == "media_commit") {
        expected_keys = keys_with({"media_sequence"});
    } else if (type == "cancel_generation" || type == "stop") {
        expected_keys = kCommonKeys;
    } else if (type == "ping") {
        expected_keys = keys_with({"nonce"});
    } else {
        throw ProtocolError("unknown control command");
    }
    require_exact_keys(command, expected_keys);

    const auto session_id = require_string(command, "session_id", kMaxIdentityBytes);
    const auto segment_id = require_string(command, "segment_id", kMaxIdentityBytes);
    require_safe_identifier(session_id, "session_id");
    require_safe_identifier(segment_id, "segment_id");
    const auto context_epoch = require_positive_u64(command, "context_epoch");
    const auto sequence = require_positive_u64(command, "sequence");

    if (type == "hello") {
        if (initialized_) {
            throw ProtocolError("duplicate hello");
        }
        const auto protocol_version = require_positive_u64(command, "protocol_version");
        if (protocol_version != kControlProtocolVersion || sequence != 1) {
            throw ProtocolError("unsupported protocol version or initial sequence");
        }

        session_id_ = session_id;
        segment_id_ = segment_id;
        context_epoch_ = context_epoch;
        last_sequence_ = sequence;
        initialized_ = true;

        auto ready = base_event("ready", session_id_, segment_id_, context_epoch_, sequence);
        ready["protocol_version"] = kControlProtocolVersion;
        ready["runtime_compatibility"] = kRuntimeCompatibility;
        ready["build_profile"] = kBuildProfile;
        ready["backend_ready"] = false;
        return {std::move(ready)};
    }

    if (!initialized_) {
        throw ProtocolError("hello must be the first command");
    }
    if (session_id != session_id_ || segment_id != segment_id_ ||
        context_epoch != context_epoch_) {
        throw ProtocolError("stale control identity");
    }
    if (last_sequence_ == std::numeric_limits<std::uint64_t>::max() ||
        sequence != last_sequence_ + 1U) {
        throw ProtocolError("out-of-order control sequence");
    }
    last_sequence_ = sequence;

    if (type == "load") {
        if (loaded_) {
            throw ProtocolError("duplicate load");
        }
        const auto manifest_digest = require_string(command, "manifest_digest", 64);
        const auto model_version = require_string(command, "model_version", 128);
        require_sha256_hex(manifest_digest, "manifest_digest");
        loaded_ = true;

        auto progress = base_event(
            "load_progress", session_id_, segment_id_, context_epoch_, sequence
        );
        progress["stage"] = "contract";
        progress["completed"] = 1;
        progress["total"] = 1;

        auto model_ready =
            base_event("model_ready", session_id_, segment_id_, context_epoch_, sequence);
        model_ready["model_version"] = model_version;
        model_ready["manifest_digest"] = manifest_digest;
        model_ready["backend_ready"] = false;
        model_ready["reason"] = "contract_backend";
        return {std::move(progress), std::move(model_ready)};
    }

    if (type == "context_begin") {
        if (!loaded_ || context_active_) {
            throw ProtocolError("context cannot begin in the current state");
        }
        const auto context_kind = require_string(command, "context_kind", 64);
        if (context_kind != "duplex_conversation" &&
            context_kind != "structured_perception" &&
            context_kind != "assistance_summary") {
            throw ProtocolError("unknown context kind");
        }
        const auto token_budget = require_bounded_u64(command, "token_budget", 32768);
        const auto audio_budget_ms =
            require_bounded_nonnegative_u64(command, "audio_budget_ms", 10U * 60U * 1000U);
        const auto frame_budget =
            require_bounded_nonnegative_u64(command, "frame_budget", 4096);
        const auto video_width =
            require_bounded_nonnegative_u64(command, "video_width", 8192);
        const auto video_height =
            require_bounded_nonnegative_u64(command, "video_height", 8192);
        if ((video_width == 0U) != (video_height == 0U) ||
            video_width * video_height > (kMaxBgraBytes / 4U)) {
            throw ProtocolError("video dimensions exceed the bounded context surface");
        }
        context_active_ = true;

        auto event =
            base_event("context_ready", session_id_, segment_id_, context_epoch_, sequence);
        event["context_kind"] = context_kind;
        event["token_budget"] = token_budget;
        event["audio_budget_ms"] = audio_budget_ms;
        event["frame_budget"] = frame_budget;
        event["video_width"] = video_width;
        event["video_height"] = video_height;
        return {std::move(event)};
    }

    if (type == "context_rotate") {
        if (!context_active_) {
            throw ProtocolError("context is not active");
        }
        const auto next_epoch = require_positive_u64(command, "next_context_epoch");
        if (context_epoch_ == std::numeric_limits<std::uint64_t>::max() ||
            next_epoch != context_epoch_ + 1U) {
            throw ProtocolError("context rotation must advance exactly one epoch");
        }
        const auto reason = require_string(command, "reason", 64);
        const auto public_summary =
            require_string(command, "public_summary", kMaxSummaryBytes, true);
        context_epoch_ = next_epoch;
        last_media_sequence_ = 0;

        auto event =
            base_event("context_rotated", session_id_, segment_id_, context_epoch_, sequence);
        event["reason"] = reason;
        event["public_summary"] = public_summary;
        return {std::move(event)};
    }

    if (type == "media_commit") {
        if (!context_active_) {
            throw ProtocolError("context is not active");
        }
        const auto media_sequence = require_positive_u64(command, "media_sequence");
        if (media_sequence <= last_media_sequence_) {
            throw ProtocolError("media sequence is stale");
        }
        last_media_sequence_ = media_sequence;

        auto event = base_event("decision", session_id_, segment_id_, context_epoch_, sequence);
        event["decision"] = "listen";
        event["text"] = "";
        event["confidence"] = 0.0;
        event["grounding"] = json::array();
        event["backend_ready"] = false;
        return {std::move(event)};
    }

    if (type == "cancel_generation") {
        auto event =
            base_event("diagnostic", session_id_, segment_id_, context_epoch_, sequence);
        event["code"] = "generation_cancelled";
        event["severity"] = "info";
        return {std::move(event)};
    }

    if (type == "ping") {
        const auto nonce = require_string(command, "nonce", kMaxNonceBytes);
        auto event = base_event("pong", session_id_, segment_id_, context_epoch_, sequence);
        event["nonce"] = nonce;
        return {std::move(event)};
    }

    stopped_ = true;
    context_active_ = false;
    loaded_ = false;
    auto event = base_event("stopped", session_id_, segment_id_, context_epoch_, sequence);
    event["reason"] = "requested";
    return {std::move(event)};
}

bool ControlRuntime::stopped() const noexcept {
    return stopped_;
}

std::optional<json> ControlRuntime::diagnostic_event(const std::string_view code) const {
    if (!initialized_ || stopped_ || code.empty() || code.size() > 96U) {
        return std::nullopt;
    }
    auto event =
        base_event("diagnostic", session_id_, segment_id_, context_epoch_, last_sequence_);
    event["code"] = code;
    event["severity"] = "error";
    return event;
}

} // namespace fairy::omni
