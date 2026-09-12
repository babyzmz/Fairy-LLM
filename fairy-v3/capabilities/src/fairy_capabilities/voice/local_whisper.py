"""Local-only dictation. One bounded CPU worker; no credentials or HTTP client."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from fairy_core.providers import (
    CancellationToken,
    ModelDelta,
    ModelRequest,
    ProviderCapability,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderKind,
    ProviderProfile,
    ProviderUnavailableError,
)
from fairy_core.voice import TranscriptionRequest, TranscriptionResult, VoiceSynthesisRequest


class LocalWhisperAdapter:
    def __init__(self, root: Path, *, python: Path | None = None, worker: Path | None = None):
        self.root = root
        self.python = python or root / ".venv" / "Scripts" / "python.exe"
        self.worker = worker or Path(__file__).with_name("local_whisper_worker.py")
        self.profile = ProviderProfile.create(
            profile_id="local-whisper-small",
            display_name="Local dictation · Whisper Small",
            kind=ProviderKind.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1",
            model_id="local/whisper-small",
            capabilities=frozenset({ProviderCapability.TEXT, ProviderCapability.STT}),
            credential_ref=None,
            fallback_profile_id=None,
            timeout_seconds=90,
            enabled=True,
        )
        self._lock = threading.Lock()
        self._closed = threading.Event()

    @property
    def credential_configured(self) -> bool:
        return True

    def health(self) -> ProviderHealth:
        ready = (
            self.python.is_file()
            and self.worker.is_file()
            and all(
                (self.root / "model" / name).is_file()
                for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
            )
        )
        return ProviderHealth(
            self.profile.id,
            ProviderHealthStatus.AVAILABLE if ready else ProviderHealthStatus.UNAVAILABLE,
            None if ready else "LOCAL_STT_NOT_INSTALLED",
            (),
        )

    def close(self) -> None:
        self._closed.set()

    def stream(
        self, request: ModelRequest, cancellation: CancellationToken
    ) -> Iterator[ModelDelta]:
        # Legacy provider metadata requires TEXT; this reserved profile is never
        # a chat model and is absent from the user/model routing catalog.
        raise ProviderUnavailableError("Local dictation cannot execute chat requests")

    def synthesize(self, request: VoiceSynthesisRequest, cancellation: CancellationToken):
        raise ProviderUnavailableError("Local dictation cannot synthesize speech")

    def transcribe(
        self, request: TranscriptionRequest, cancellation: CancellationToken
    ) -> TranscriptionResult:
        if self._closed.is_set():
            raise ProviderUnavailableError("Local transcription is shutting down")
        if request.profile_id != self.profile.id:
            raise ValueError("Local transcription profile mismatch")
        if self.health().status != ProviderHealthStatus.AVAILABLE:
            raise ProviderUnavailableError("Local transcription runtime/model is not installed")
        if not self._lock.acquire(blocking=False):
            raise ProviderUnavailableError(
                "Local transcription is busy; try again when it finishes"
            )
        process = None
        try:
            cancellation.raise_if_cancelled()
            environment = {
                key: os.environ[key]
                for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
                if key in os.environ
            }
            environment.update(
                HF_HUB_OFFLINE="1",
                HF_HUB_DISABLE_TELEMETRY="1",
                HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
                OMP_NUM_THREADS="4",
                PYTHONUTF8="1",
            )
            process = subprocess.Popen(
                [str(self.python), "-I", str(self.worker), str(self.root / "model")],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            payload = json.dumps(
                {
                    "audio_base64": base64.b64encode(request.audio).decode("ascii"),
                    "language": request.language,
                }
            ).encode()
            deadline = time.monotonic() + self.profile.timeout_seconds
            while True:
                cancellation.raise_if_cancelled()
                if self._closed.is_set() or time.monotonic() >= deadline:
                    raise ProviderUnavailableError("Local transcription stopped or timed out")
                try:
                    output, _ = process.communicate(payload, timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    payload = None
            if process.returncode != 0 or len(output) > 400_000:
                raise ProviderUnavailableError(
                    "Local transcription failed; check the recording or local runtime"
                )
            result = json.loads(output)
            if result.get("error") == "NO_SPEECH":
                raise ProviderUnavailableError("No speech detected. Try recording again.")
            return TranscriptionResult.create(
                profile_id=self.profile.id,
                text=result["text"],
                language=result.get("language"),
                segments=(),
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ProviderUnavailableError(
                "Local transcription returned an invalid result"
            ) from error
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.communicate()
            self._lock.release()
