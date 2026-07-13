from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace

import pytest

from fairy_voice_worker.protocol import OneTimeTokenRegistry, TokenRejectedError
from fairy_voice_worker.runtime import (
    INITIAL_TOKEN_HOP,
    CosyVoice3Runtime,
    VoiceWorkerHealth,
    _install_frozen_runtime_guards,
    normalize_spoken_text,
)
from fairy_voice_worker.server import VoiceWorkerState, create_server


def test_one_time_token_is_consumed_and_replay_is_rejected() -> None:
    registry = OneTimeTokenRegistry()
    token = "a" * 48
    registry.register(token)
    registry.consume(token)
    with pytest.raises(TokenRejectedError, match="already used"):
        registry.consume(token)


def test_spoken_text_removes_code_links_and_raw_markup() -> None:
    assert (
        normalize_spoken_text("**See** [Fairy](https://fairy.test) `secret` now")
        == "See Fairy now\u3002"
    )


def test_cosyvoice_cannot_download_an_unverified_model_at_runtime() -> None:
    _install_frozen_runtime_guards()
    with pytest.raises(RuntimeError, match="downloads are disabled"):
        sys.modules["modelscope"].snapshot_download("untrusted/model")  # type: ignore[attr-defined]

    def original() -> str:
        return "unchanged"

    decorated = sys.modules["typeguard"].typechecked(original)  # type: ignore[attr-defined]
    assert decorated is original


def test_cosyvoice_prime_uses_bounded_text_with_streaming_audio() -> None:
    runtime = CosyVoice3Runtime(
        model_dir=Path("model"),
        source_dir=Path("source"),
        prompt_wav=Path("prompt.wav"),
        prompt_text=Path("prompt.txt"),
    )
    model = RecordingCosyVoiceModel()
    runtime._model = model

    runtime._prime()

    assert model.model.token_hop_len == INITIAL_TOKEN_HOP
    assert model.received_text == "Fairy 已准备好继续工作。"
    assert model.received_text_type is str


def test_cosyvoice_synthesis_uses_bounded_normalized_text() -> None:
    runtime = CosyVoice3Runtime(
        model_dir=Path("model"),
        source_dir=Path("source"),
        prompt_wav=Path("prompt.wav"),
        prompt_text=Path("prompt.txt"),
    )
    model = RecordingCosyVoiceModel()
    runtime._model = model

    assert list(runtime.synthesize("Hello", threading.Event())) == []

    assert model.received_text == "Hello。"
    assert model.received_text_type is str


def test_cosyvoice_does_not_report_ready_until_prime_completes() -> None:
    runtime = CosyVoice3Runtime(
        model_dir=Path("model"),
        source_dir=Path("source"),
        prompt_wav=Path("prompt.wav"),
        prompt_text=Path("prompt.txt"),
    )
    runtime._model = RecordingCosyVoiceModel()
    runtime._model_digest = "a" * 64

    assert runtime.health().status != "ready"

    runtime._ready = True

    assert runtime.health().status == "ready"


def test_loopback_server_streams_pcm_and_rejects_token_replay() -> None:
    runtime = FakeRuntime()
    state = VoiceWorkerState(runtime=runtime, bootstrap_token="bootstrap-" + "b" * 32)
    server = create_server(host="127.0.0.1", port=0, state=state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        token = "session-" + "s" * 40
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
        connection.request(
            "POST",
            "/v1/tokens",
            body=f'{{"token":"{token}"}}',
            headers={
                "Authorization": f"Bearer {state.bootstrap_token}",
                "Content-Type": "application/json",
            },
        )
        assert connection.getresponse().status == 201
        connection.close()

        request_body = '{"session_id":"session-1","text":"Hello","scope_digest":"' + "a" * 64 + '"}'
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
        connection.request(
            "POST",
            "/v1/sessions",
            body=request_body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Type") == "application/vnd.fairy.pcm16"
        assert response.read() == b"\x00\x00\x01\x00"
        connection.close()

        replay = HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
        replay.request(
            "POST",
            "/v1/sessions",
            body=request_body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        assert replay.getresponse().status == 401
        replay.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_server_refuses_non_loopback_bindings() -> None:
    with pytest.raises(ValueError, match=r"127\.0\.0\.1"):
        create_server(
            host="0.0.0.0",
            port=0,
            state=VoiceWorkerState(runtime=FakeRuntime(), bootstrap_token="b" * 40),
        )


class FakeRuntime:
    @property
    def sample_rate(self) -> int:
        return 24_000

    def health(self) -> VoiceWorkerHealth:
        return VoiceWorkerHealth(
            status="ready",
            model_repository="fixture",
            model_installed=True,
            model_ready=True,
            model_digest="a" * 64,
            prompt_ready=True,
            cuda_available=True,
            tensorrt_available=True,
            backend="fixture",
            device_name="Fixture GPU",
            sample_rate=24_000,
            error_code=None,
        )

    def load(self) -> None:
        return None

    def synthesize(self, text: str, cancellation: threading.Event) -> Iterator[bytes]:
        assert text == "Hello"
        if not cancellation.is_set():
            yield b"\x00\x00\x01\x00"


class RecordingCosyVoiceModel:
    def __init__(self) -> None:
        self.model = SimpleNamespace(token_hop_len=25)
        self.received_text = ""
        self.received_text_type: type[object] | None = None

    def inference_zero_shot(
        self,
        text: str | Iterator[str],
        _prompt_text: str,
        _prompt_wav: str,
        *,
        zero_shot_spk_id: str,
        stream: bool,
    ) -> Iterator[dict[str, object]]:
        assert zero_shot_spk_id == "fairy-v3"
        assert stream is True
        self.received_text_type = type(text)
        self.received_text = text if isinstance(text, str) else "".join(text)
        return iter(())
