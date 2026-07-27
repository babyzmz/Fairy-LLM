#include "fairy_omni/media.hpp"

#include "fairy_omni/control.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <limits>
#include <mutex>
#include <string>
#include <thread>
#include <utility>

#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#include <tlhelp32.h>
#endif

namespace fairy::omni {

class MediaPipePump::Impl final {
  public:
    Impl(std::filesystem::path name, MediaBuffer &target)
        : pipe_name(std::move(name)), buffer(target) {}

    ~Impl() {
        stop();
    }

    void start() {
        if (worker.joinable()) {
            throw ProtocolError("media pipe pump already started");
        }
        stopping.store(false);
        worker = std::thread([this] { run(); });

        std::unique_lock lock(state_mutex);
        if (!state_changed.wait_for(lock, std::chrono::seconds(5), [this] {
                return connected || error.has_value();
            })) {
            lock.unlock();
            stop();
            throw ProtocolError("media pipe connection timed out");
        }
        if (error.has_value()) {
            const auto code = *error;
            lock.unlock();
            stop();
            throw ProtocolError(code);
        }
    }

    void stop() noexcept {
        stopping.store(true);
#ifdef _WIN32
        if (worker.joinable()) {
            static_cast<void>(CancelSynchronousIo(worker.native_handle()));
        }
#endif
        if (worker.joinable()) {
            worker.join();
        }
    }

    std::optional<std::string> take_error() {
        const std::scoped_lock lock(state_mutex);
        auto current = std::move(error);
        error.reset();
        return current;
    }

  private:
#ifdef _WIN32
    static std::uint32_t parent_process_id() {
        const auto current = GetCurrentProcessId();
        const auto snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if (snapshot == INVALID_HANDLE_VALUE) {
            throw ProtocolError("media_parent_probe_failed");
        }

        PROCESSENTRY32W entry{};
        entry.dwSize = sizeof(entry);
        std::uint32_t parent = 0;
        if (Process32FirstW(snapshot, &entry) != FALSE) {
            do {
                if (entry.th32ProcessID == current) {
                    parent = entry.th32ParentProcessID;
                    break;
                }
            } while (Process32NextW(snapshot, &entry) != FALSE);
        }
        CloseHandle(snapshot);
        if (parent == 0U) {
            throw ProtocolError("media_parent_probe_failed");
        }
        return parent;
    }

    static std::wstring validated_pipe_path(const std::filesystem::path &name) {
        const auto token = name.native();
        if (token.empty() || token.size() > 128U) {
            throw ProtocolError("media_pipe_name_invalid");
        }
        for (const auto character : token) {
            const auto safe = (character >= L'a' && character <= L'z') ||
                              (character >= L'A' && character <= L'Z') ||
                              (character >= L'0' && character <= L'9') ||
                              character == L'-' || character == L'_' || character == L'.';
            if (!safe) {
                throw ProtocolError("media_pipe_name_invalid");
            }
        }
        return L"\\\\.\\pipe\\" + token;
    }

    bool read_exact(const HANDLE pipe, std::uint8_t *output, const std::size_t length) {
        std::size_t offset = 0;
        while (offset < length && !stopping.load()) {
            DWORD read = 0;
            const auto remaining = length - offset;
            const auto chunk = static_cast<DWORD>(
                (remaining > static_cast<std::size_t>(std::numeric_limits<DWORD>::max()))
                    ? std::numeric_limits<DWORD>::max()
                    : remaining
            );
            if (ReadFile(pipe, output + offset, chunk, &read, nullptr) == FALSE) {
                const auto code = GetLastError();
                if (stopping.load() &&
                    (code == ERROR_OPERATION_ABORTED || code == ERROR_BROKEN_PIPE)) {
                    return false;
                }
                throw ProtocolError("media_pipe_read_failed");
            }
            if (read == 0U) {
                if (stopping.load()) {
                    return false;
                }
                throw ProtocolError("media_pipe_closed");
            }
            offset += read;
        }
        return offset == length;
    }

    void run_windows() {
        const auto path = validated_pipe_path(pipe_name);
        HANDLE pipe = INVALID_HANDLE_VALUE;
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (!stopping.load() && std::chrono::steady_clock::now() < deadline) {
            pipe = CreateFileW(
                path.c_str(),
                GENERIC_READ,
                0,
                nullptr,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                nullptr
            );
            if (pipe != INVALID_HANDLE_VALUE) {
                break;
            }
            if (GetLastError() != ERROR_PIPE_BUSY && GetLastError() != ERROR_FILE_NOT_FOUND) {
                throw ProtocolError("media_pipe_connect_failed");
            }
            static_cast<void>(WaitNamedPipeW(path.c_str(), 50));
        }
        if (pipe == INVALID_HANDLE_VALUE) {
            throw ProtocolError("media_pipe_connect_failed");
        }

        try {
            ULONG server_process = 0;
            if (GetNamedPipeServerProcessId(pipe, &server_process) == FALSE ||
                server_process != parent_process_id()) {
                throw ProtocolError("media_pipe_server_identity_mismatch");
            }
            {
                const std::scoped_lock lock(state_mutex);
                connected = true;
                state_changed.notify_all();
            }

            while (!stopping.load()) {
                std::array<std::uint8_t, kMediaHeaderBytes> header_bytes{};
                if (!read_exact(pipe, header_bytes.data(), header_bytes.size())) {
                    break;
                }
                MediaHeader header;
                try {
                    header = decode_media_header(header_bytes);
                    buffer.validate_before_allocation(header);
                } catch (const ProtocolError &) {
                    throw ProtocolError("media_frame_rejected");
                }
                std::vector<std::uint8_t> payload(header.payload_length);
                if (!read_exact(pipe, payload.data(), payload.size())) {
                    break;
                }
                try {
                    buffer.commit(header, std::move(payload));
                } catch (const ProtocolError &) {
                    throw ProtocolError("media_frame_rejected");
                }
            }
        } catch (...) {
            CloseHandle(pipe);
            throw;
        }
        CloseHandle(pipe);
    }
#endif

    void run() noexcept {
        try {
#ifdef _WIN32
            run_windows();
#else
            throw ProtocolError("media_pipe_unsupported_platform");
#endif
        } catch (const std::exception &failure) {
            const std::scoped_lock lock(state_mutex);
            if (!stopping.load()) {
                auto code = std::string(failure.what());
                if (code.size() > 96U) {
                    code = "media_pipe_failure";
                }
                error = std::move(code);
            }
            state_changed.notify_all();
        }
    }

    std::filesystem::path pipe_name;
    MediaBuffer &buffer;
    std::atomic_bool stopping{false};
    std::thread worker;
    std::mutex state_mutex;
    std::condition_variable state_changed;
    bool connected = false;
    std::optional<std::string> error;
};

MediaPipePump::MediaPipePump(std::filesystem::path pipe_name, MediaBuffer &buffer)
    : impl_(std::make_unique<Impl>(std::move(pipe_name), buffer)) {}

MediaPipePump::~MediaPipePump() = default;

void MediaPipePump::start() {
    impl_->start();
}

void MediaPipePump::stop() noexcept {
    impl_->stop();
}

std::optional<std::string> MediaPipePump::take_error() {
    return impl_->take_error();
}

} // namespace fairy::omni
