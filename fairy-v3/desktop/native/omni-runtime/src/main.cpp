#include "fairy_omni/control.hpp"
#include "fairy_omni/identity.hpp"
#include "fairy_omni/media.hpp"

#include <algorithm>
#include <filesystem>
#include <iostream>
#include <string>
#include <string_view>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

namespace {

struct Arguments {
    bool self_test = false;
    bool stdio = false;
    std::filesystem::path manifest;
    std::filesystem::path model_root;
    std::filesystem::path media_pipe;
};

template <typename Character>
Arguments parse_arguments(const int argc, Character **argv) {
    Arguments result;
    for (int index = 1; index < argc; ++index) {
        const std::filesystem::path argument(argv[index]);
        if (argument == std::filesystem::path("--self-test") && !result.self_test &&
            !result.stdio) {
            result.self_test = true;
        } else if (argument == std::filesystem::path("--stdio") && !result.stdio &&
                   !result.self_test) {
            result.stdio = true;
        } else if (argument == std::filesystem::path("--manifest") &&
                   result.manifest.empty() && index + 1 < argc) {
            result.manifest = std::filesystem::path(argv[++index]);
        } else if (argument == std::filesystem::path("--model-root") &&
                   result.model_root.empty() && index + 1 < argc) {
            result.model_root = std::filesystem::path(argv[++index]);
        } else if (argument == std::filesystem::path("--media-pipe") &&
                   result.media_pipe.empty() && index + 1 < argc) {
            result.media_pipe = argv[++index];
        } else {
            throw fairy::omni::ProtocolError("invalid or duplicate command-line argument");
        }
    }

    if (result.self_test) {
        if (result.manifest.empty() || result.model_root.empty() || !result.media_pipe.empty()) {
            throw fairy::omni::ProtocolError("self-test requires manifest and model root only");
        }
    } else if (result.stdio) {
        if (result.media_pipe.empty() || !result.manifest.empty() || !result.model_root.empty()) {
            throw fairy::omni::ProtocolError("stdio mode requires exactly one media pipe");
        }
    } else {
        throw fairy::omni::ProtocolError("a runtime mode is required");
    }
    return result;
}

void emit_bounded_error(const std::exception &error) {
    auto message = std::string(error.what());
    if (message.size() > 512U) {
        message.resize(512U);
    }
    std::replace(message.begin(), message.end(), '\r', ' ');
    std::replace(message.begin(), message.end(), '\n', ' ');
    std::cerr << "fairy-omni-runtime: " << message << '\n';
}

} // namespace

template <typename Character>
int run(const int argc, Character **argv) {
    try {
        const auto arguments = parse_arguments(argc, argv);
        if (arguments.self_test) {
            std::cout << fairy::omni::build_self_test_report(
                             arguments.manifest, arguments.model_root
                         )
                             .dump()
                      << '\n';
            return 0;
        }

#ifdef _WIN32
        if (_setmode(_fileno(stdin), _O_BINARY) == -1 ||
            _setmode(_fileno(stdout), _O_BINARY) == -1) {
            throw fairy::omni::ProtocolError("failed to set binary stdio mode");
        }
#endif

        fairy::omni::ControlRuntime runtime;
        fairy::omni::MediaBuffer media_buffer;
        fairy::omni::MediaPipePump media_pipe(arguments.media_pipe, media_buffer);
        media_pipe.start();
        while (!runtime.stopped()) {
            auto frame = fairy::omni::read_frame(std::cin);
            if (frame.status == fairy::omni::FrameReadStatus::eof) {
                return 0;
            }
            if (const auto media_error = media_pipe.take_error(); media_error.has_value()) {
                if (const auto diagnostic = runtime.diagnostic_event(*media_error);
                    diagnostic.has_value()) {
                    fairy::omni::write_frame(std::cout, *diagnostic);
                }
                return 2;
            }

            const auto events = runtime.handle(frame.payload);
            const auto &type = frame.payload.at("type").get_ref<const std::string &>();
            if (type == "hello") {
                media_buffer.rotate_epoch(frame.payload.at("context_epoch").get<std::uint64_t>());
            } else if (type == "context_begin") {
                media_buffer.configure_video(
                    frame.payload.at("video_width").get<std::uint32_t>(),
                    frame.payload.at("video_height").get<std::uint32_t>()
                );
            } else if (type == "context_rotate") {
                media_buffer.rotate_epoch(
                    frame.payload.at("next_context_epoch").get<std::uint64_t>()
                );
            }

            for (const auto &event : events) {
                fairy::omni::write_frame(std::cout, event);
            }
            if (runtime.stopped()) {
                media_pipe.stop();
            }
        }
        return 0;
    } catch (const std::exception &error) {
        emit_bounded_error(error);
        return 2;
    }
}

#ifdef _WIN32
int wmain(const int argc, wchar_t **argv) {
    return run(argc, argv);
}
#else
int main(const int argc, char **argv) {
    return run(argc, argv);
}
#endif
