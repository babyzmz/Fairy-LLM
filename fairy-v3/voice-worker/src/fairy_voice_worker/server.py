from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import warnings
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from fairy_voice_worker.protocol import (
    OneTimeTokenRegistry,
    TokenRejectedError,
    bearer_token,
    constant_time_token_matches,
)
from fairy_voice_worker.runtime import VoiceRuntime, install_model, runtime_from_environment

MAX_REQUEST_BYTES = 64 * 1024


class VoiceWorkerState:
    def __init__(self, *, runtime: VoiceRuntime, bootstrap_token: str) -> None:
        self.runtime = runtime
        self.bootstrap_token = bootstrap_token
        self.tokens = OneTimeTokenRegistry()
        self._cancellations: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._prepare_lock = threading.Lock()

    def prepare_runtime(self) -> dict[str, object]:
        with self._prepare_lock:
            health = self.runtime.health()
            if health.status != "ready":
                self.runtime.load()
                health = self.runtime.health()
            if health.status != "ready":
                raise RuntimeError(health.error_code or "VOICE_WORKER_NOT_READY")
            return health.as_dict()

    def cancellation_for(self, session_id: str) -> threading.Event:
        if not session_id or len(session_id) > 128:
            raise ValueError("voice session id is invalid")
        with self._lock:
            cancellation = self._cancellations.get(session_id)
            if cancellation is not None:
                raise ValueError("voice session is already active")
            cancellation = threading.Event()
            self._cancellations[session_id] = cancellation
            return cancellation

    def cancel(self, session_id: str) -> bool:
        with self._lock:
            cancellation = self._cancellations.get(session_id)
        if cancellation is None:
            return False
        cancellation.set()
        return True

    def release(self, session_id: str) -> None:
        with self._lock:
            self._cancellations.pop(session_id, None)


class VoiceRequestHandler(BaseHTTPRequestHandler):
    server_version = "FairyVoiceWorker/1"
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> VoiceWorkerState:
        return self.server.state  # type: ignore[attr-defined, no-any-return]

    def do_GET(self) -> None:
        if self.path != "/v1/health":
            self._json(HTTPStatus.NOT_FOUND, {"error_code": "NOT_FOUND"})
            return
        if not self._authorize_control():
            return
        self._json(HTTPStatus.OK, self.state.runtime.health().as_dict())

    def do_POST(self) -> None:
        if self.path == "/v1/tokens":
            self._register_token()
            return
        if self.path == "/v1/model/install":
            self._install_model()
            return
        if self.path == "/v1/runtime/prepare":
            self._prepare_runtime()
            return
        if self.path == "/v1/sessions":
            self._stream_session()
            return
        self._json(HTTPStatus.NOT_FOUND, {"error_code": "NOT_FOUND"})

    def do_DELETE(self) -> None:
        prefix = "/v1/sessions/"
        if not self.path.startswith(prefix):
            self._json(HTTPStatus.NOT_FOUND, {"error_code": "NOT_FOUND"})
            return
        if not self._consume_session_token():
            return
        session_id = self.path.removeprefix(prefix)
        cancelled = self.state.cancel(session_id)
        self._json(HTTPStatus.OK, {"session_id": session_id, "cancelled": cancelled})

    def _register_token(self) -> None:
        if not self._authorize_control():
            return
        try:
            payload = self._read_json()
            token = str(payload["token"])
            self.state.tokens.register(token)
        except (KeyError, ValueError, TokenRejectedError) as error:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error_code": "VOICE_TOKEN_INVALID", "message": str(error)},
            )
            return
        self._json(HTTPStatus.CREATED, {"registered": True})

    def _install_model(self) -> None:
        if not self._authorize_control():
            return
        data_dir = os.environ.get("FAIRY_VOICE_DATA_DIR")
        if not data_dir:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error_code": "VOICE_DATA_DIR_UNAVAILABLE"},
            )
            return
        try:
            digest = install_model(Path(data_dir) / "models" / "Fun-CosyVoice3-0.5B-2512")
            self.state.runtime.load()
        except Exception:
            logging.exception("Fairy Voice model installation failed")
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error_code": "VOICE_MODEL_INSTALL_FAILED"},
            )
            return
        self._json(HTTPStatus.OK, {"installed": True, "manifest_digest": digest})

    def _prepare_runtime(self) -> None:
        if not self._authorize_control():
            return
        try:
            health = self.state.prepare_runtime()
        except Exception:
            logging.exception("Fairy Voice runtime preparation failed")
            failed_health = self.state.runtime.health()
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    **failed_health.as_dict(),
                    "error_code": failed_health.error_code or "VOICE_WORKER_LOAD_FAILED",
                },
            )
            return
        self._json(HTTPStatus.OK, health)

    def _stream_session(self) -> None:
        if not self._consume_session_token():
            return
        try:
            payload = self._read_json()
            session_id = str(payload["session_id"])
            text = str(payload["text"])
            scope_digest = str(payload["scope_digest"])
            if len(scope_digest) != 64 or any(
                value not in "0123456789abcdef" for value in scope_digest
            ):
                raise ValueError("voice scope digest is invalid")
            cancellation = self.state.cancellation_for(session_id)
        except (KeyError, ValueError) as error:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error_code": "VOICE_REQUEST_INVALID", "message": str(error)},
            )
            return
        health = self.state.runtime.health()
        if health.status != "ready":
            self.state.release(session_id)
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error_code": health.error_code or "VOICE_WORKER_NOT_READY"},
            )
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/vnd.fairy.pcm16")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Fairy-Session-Id", session_id)
        self.send_header("X-Fairy-Scope-Digest", scope_digest)
        self.send_header("X-Fairy-Sample-Rate", str(self.state.runtime.sample_rate))
        self.send_header("X-Fairy-Channels", "1")
        self.end_headers()
        try:
            for pcm in self.state.runtime.synthesize(text, cancellation):
                if cancellation.is_set():
                    break
                self.wfile.write(pcm)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            cancellation.set()
        finally:
            self.close_connection = True
            self.state.release(session_id)

    def _authorize_control(self) -> bool:
        try:
            token = bearer_token(self.headers.get("Authorization"))
        except TokenRejectedError:
            self._json(HTTPStatus.UNAUTHORIZED, {"error_code": "VOICE_AUTH_REQUIRED"})
            return False
        if not constant_time_token_matches(token, self.state.bootstrap_token):
            self._json(HTTPStatus.FORBIDDEN, {"error_code": "VOICE_AUTH_REJECTED"})
            return False
        return True

    def _consume_session_token(self) -> bool:
        try:
            token = bearer_token(self.headers.get("Authorization"))
            self.state.tokens.consume(token)
        except TokenRejectedError:
            self._json(HTTPStatus.UNAUTHORIZED, {"error_code": "VOICE_TOKEN_REJECTED"})
            return False
        return True

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("voice content length is invalid") from error
        if not 1 <= length <= MAX_REQUEST_BYTES:
            raise ValueError("voice request size is invalid")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("voice request must be an object")
        return payload

    def _json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        content = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        return


class VoiceHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: VoiceWorkerState) -> None:
        super().__init__(address, VoiceRequestHandler)
        self.state = state

    def handle_error(self, request: object, client_address: object) -> None:
        # Client cancellation is part of the streaming protocol; never emit tracebacks.
        return


def create_server(*, host: str, port: int, state: VoiceWorkerState) -> VoiceHttpServer:
    if host != "127.0.0.1":
        raise ValueError("Fairy Voice Worker must bind to 127.0.0.1")
    if not 0 <= port <= 65_535:
        raise ValueError("voice worker port is invalid")
    return VoiceHttpServer((host, port), state)


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    warnings.filterwarnings("ignore", category=FutureWarning)
    os.environ.setdefault("TQDM_DISABLE", "1")
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    bootstrap_token = os.environ.get("FAIRY_VOICE_BOOTSTRAP_TOKEN", "")
    if len(bootstrap_token) < 32:
        raise SystemExit("FAIRY_VOICE_BOOTSTRAP_TOKEN is required")
    runtime = runtime_from_environment()
    state = VoiceWorkerState(runtime=runtime, bootstrap_token=bootstrap_token)
    server = create_server(host=args.host, port=args.port, state=state)
    port = server.server_address[1]
    print(json.dumps({"protocol": "fairy-voice-worker-v1", "port": port}), flush=True)
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    main()


__all__ = ["VoiceWorkerState", "create_server", "main"]
