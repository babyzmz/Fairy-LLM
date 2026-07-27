#include "fairy_omni/control.hpp"
#include "fairy_omni/backend.hpp"
#include "fairy_omni/identity.hpp"

#include <cstdlib>
#include <filesystem>
#include <functional>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

using fairy::omni::ControlRuntime;
using fairy::omni::ProtocolError;
using json = nlohmann::json;

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

std::filesystem::path path_from_utf8(const std::string_view value) {
    std::u8string encoded;
    encoded.reserve(value.size());
    for (const auto byte : value) {
        encoded.push_back(static_cast<char8_t>(static_cast<unsigned char>(byte)));
    }
    return std::filesystem::path(encoded);
}

json base_command(
    const char *type,
    const std::uint64_t sequence,
    const std::uint64_t context_epoch = 1
) {
    return {
        {"type", type},
        {"session_id", "session-a"},
        {"segment_id", "segment-a"},
        {"context_epoch", context_epoch},
        {"sequence", sequence},
    };
}

json hello() {
    auto value = base_command("hello", 1);
    value["protocol_version"] = 1;
    return value;
}

void test_frames() {
    const json payload{{"type", "ping"}, {"nonce", "safe"}};
    const auto frame = fairy::omni::encode_frame(payload);
    std::string bytes(frame.begin(), frame.end());
    std::istringstream input(bytes, std::ios::binary);
    const auto decoded = fairy::omni::read_frame(input);
    require(decoded.status == fairy::omni::FrameReadStatus::frame, "frame was not read");
    require(decoded.payload == payload, "frame payload changed");
    require(
        fairy::omni::read_frame(input).status == fairy::omni::FrameReadStatus::eof,
        "clean EOF was not preserved"
    );

    require_protocol_error(
        [] {
            std::istringstream truncated(std::string("\x01\x00", 2), std::ios::binary);
            static_cast<void>(fairy::omni::read_frame(truncated));
        },
        "truncated prefix"
    );
    require_protocol_error(
        [] {
            std::istringstream empty(std::string("\x00\x00\x00\x00", 4), std::ios::binary);
            static_cast<void>(fairy::omni::read_frame(empty));
        },
        "empty payload"
    );
    require_protocol_error(
        [] {
            std::string oversized("\x01\x00\x04\x00", 4);
            std::istringstream input(oversized, std::ios::binary);
            static_cast<void>(fairy::omni::read_frame(input));
        },
        "oversized payload"
    );
    require_protocol_error(
        [] {
            std::string invalid("\x02\x00\x00\x00{}", 6);
            invalid[4] = static_cast<char>(0xC3);
            invalid[5] = static_cast<char>(0x28);
            std::istringstream input(invalid, std::ios::binary);
            static_cast<void>(fairy::omni::read_frame(input));
        },
        "invalid UTF-8"
    );
    require_protocol_error(
        [] {
            const std::string duplicate = R"({"type":"ping","type":"stop"})";
            std::string framed;
            const auto length = static_cast<std::uint32_t>(duplicate.size());
            framed.push_back(static_cast<char>(length & 0xFFU));
            framed.push_back(static_cast<char>((length >> 8U) & 0xFFU));
            framed.push_back(static_cast<char>((length >> 16U) & 0xFFU));
            framed.push_back(static_cast<char>((length >> 24U) & 0xFFU));
            framed += duplicate;
            std::istringstream input(framed, std::ios::binary);
            static_cast<void>(fairy::omni::read_frame(input));
        },
        "duplicate key"
    );
}

void test_lifecycle() {
    ControlRuntime runtime;
    const auto ready = runtime.handle(hello());
    require(ready.size() == 1 && ready[0].at("type") == "ready", "ready event missing");
    require(!ready[0].at("backend_ready").get<bool>(), "contract backend claimed ready");

    auto load = base_command("load", 2);
    load["manifest_digest"] = std::string(64, 'a');
    load["model_version"] = "fixture";
    const auto load_events = runtime.handle(load);
    require(
        load_events.size() == 2 && load_events[1].at("type") == "model_ready",
        "load events missing"
    );
    require(
        !load_events[1].at("backend_ready").get<bool>(),
        "contract model claimed ready"
    );

    auto begin = base_command("context_begin", 3);
    begin["context_kind"] = "duplex_conversation";
    begin["token_budget"] = 4096;
    begin["audio_budget_ms"] = 60000;
    begin["frame_budget"] = 120;
    begin["video_width"] = 1280;
    begin["video_height"] = 720;
    require(
        runtime.handle(begin).at(0).at("type") == "context_ready",
        "context did not begin"
    );

    auto media = base_command("media_commit", 4);
    media["media_sequence"] = 1;
    const auto decision = runtime.handle(media).at(0);
    require(decision.at("decision") == "listen", "contract decision must listen");
    require(decision.at("text") == "", "contract decision emitted text");

    auto rotate = base_command("context_rotate", 5);
    rotate["next_context_epoch"] = 2;
    rotate["reason"] = "budget";
    rotate["public_summary"] = "bounded";
    require(
        runtime.handle(rotate).at(0).at("context_epoch") == 2,
        "context epoch did not rotate"
    );

    auto ping = base_command("ping", 6, 2);
    ping["nonce"] = "nonce-a";
    require(runtime.handle(ping).at(0).at("nonce") == "nonce-a", "pong mismatch");

    auto cancel = base_command("cancel_generation", 7, 2);
    require(
        runtime.handle(cancel).at(0).at("type") == "diagnostic",
        "cancel diagnostic missing"
    );

    auto stop = base_command("stop", 8, 2);
    require(runtime.handle(stop).at(0).at("type") == "stopped", "stop event missing");
    require(runtime.stopped(), "runtime did not stop");
}

void test_fail_closed_state() {
    require_protocol_error(
        [] {
            ControlRuntime runtime;
            auto ping = base_command("ping", 1);
            ping["nonce"] = "early";
            static_cast<void>(runtime.handle(ping));
        },
        "command before hello"
    );
    require_protocol_error(
        [] {
            ControlRuntime runtime;
            auto value = hello();
            value["unknown"] = true;
            static_cast<void>(runtime.handle(value));
        },
        "unknown field"
    );
    require_protocol_error(
        [] {
            ControlRuntime runtime;
            static_cast<void>(runtime.handle(hello()));
            static_cast<void>(runtime.handle(hello()));
        },
        "duplicate hello"
    );
    require_protocol_error(
        [] {
            ControlRuntime runtime;
            static_cast<void>(runtime.handle(hello()));
            auto ping = base_command("ping", 3);
            ping["nonce"] = "skipped";
            static_cast<void>(runtime.handle(ping));
        },
        "out-of-order sequence"
    );
    require_protocol_error(
        [] {
            ControlRuntime runtime;
            static_cast<void>(runtime.handle(hello()));
            auto ping = base_command("ping", 2);
            ping["segment_id"] = "stale";
            ping["nonce"] = "wrong";
            static_cast<void>(runtime.handle(ping));
        },
        "stale identity"
    );
    require_protocol_error(
        [] {
            ControlRuntime runtime;
            static_cast<void>(runtime.handle(hello()));
            auto unknown = base_command("open_url", 2);
            static_cast<void>(runtime.handle(unknown));
        },
        "tool-shaped command"
    );
    require_protocol_error(
        [] {
            ControlRuntime runtime;
            auto value = hello();
            value["session_id"] = "unsafe\nidentity";
            static_cast<void>(runtime.handle(value));
        },
        "unsafe identity"
    );
}

void test_self_test_identity() {
    const auto backend = fairy::omni::make_backend();
    const auto status = backend->status();
    require(!status.compiled, "contract backend claimed an upstream binding");
    require(!status.cuda, "contract backend claimed CUDA");
    require(!status.ready, "contract backend claimed readiness");
    require(status.reason == "contract_backend", "contract backend reason mismatch");

    const auto report = fairy::omni::build_self_test_report(
        path_from_utf8(FAIRY_OMNI_TEST_MANIFEST),
        std::filesystem::path("unused-contract-model-root")
    );
    require(report.at("schema_version") == 2, "self-test schema is not 2");
    require(report.at("build_profile") == "contract", "self-test profile mismatch");
    require(!report.at("cuda_compiled").get<bool>(), "contract claimed CUDA");
    require(!report.at("backend_ready").get<bool>(), "contract self-test claimed ready");
    require(
        report.at("model_probe") == "not_run_contract",
        "contract self-test claimed a model probe"
    );
}

} // namespace

int main() {
    try {
        test_frames();
        test_lifecycle();
        test_fail_closed_state();
        test_self_test_identity();
        std::cout << "control tests passed\n";
        return EXIT_SUCCESS;
    } catch (const std::exception &error) {
        std::cerr << "control tests failed: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
