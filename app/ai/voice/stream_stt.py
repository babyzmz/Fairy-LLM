from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, Iterator


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class STTSegment:
    text: str
    start_seconds: float
    end_seconds: float
    is_final: bool


class StreamSTT(ABC):
    @abstractmethod
    def transcribe_stream(self, audio_frames: Iterable[bytes], *, sample_rate: int = 16000) -> Iterator[STTSegment]:
        ...

    @abstractmethod
    def transcribe_file(self, audio_path: str, *, language: str | None = None) -> list[STTSegment]:
        ...

    @property
    @abstractmethod
    def available(self) -> bool:
        ...


class DummyStreamSTT(StreamSTT):
    @property
    def available(self) -> bool:
        return False

    def transcribe_stream(self, audio_frames: Iterable[bytes], *, sample_rate: int = 16000) -> Iterator[STTSegment]:
        for _ in audio_frames:
            pass
        if False:
            yield STTSegment("", 0.0, 0.0, True)

    def transcribe_file(self, audio_path: str, *, language: str | None = None) -> list[STTSegment]:
        del audio_path, language
        return []


class WhisperStreamSTT(StreamSTT):
    def __init__(
        self,
        *,
        model_size: str = "small",
        device: str = "auto",
        compute_type: str = "int8",
        language: str | None = "zh",
        beam_size: int = 1,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self._model = None
        self._lock = threading.Lock()
        self._load_error: str | None = None

    def _ensure_loaded(self) -> object | None:
        if self._model is not None:
            return self._model
        if self._load_error is not None:
            return None
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except Exception as exc:
                logger.warning("faster_whisper_unavailable: %s", exc)
                self._load_error = str(exc)
                return None
            try:
                self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
            except Exception as exc:
                logger.exception("whisper_load_failed")
                self._load_error = str(exc)
                return None
        return self._model

    @property
    def available(self) -> bool:
        return self._ensure_loaded() is not None

    def transcribe_stream(self, audio_frames: Iterable[bytes], *, sample_rate: int = 16000) -> Iterator[STTSegment]:
        model = self._ensure_loaded()
        if model is None:
            return
        import io
        import wave

        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                for frame in audio_frames:
                    wav.writeframes(frame)
            buffer.seek(0)
            segments, _info = model.transcribe(  # type: ignore[attr-defined]
                buffer,
                language=self.language,
                beam_size=self.beam_size,
                vad_filter=True,
            )
            for segment in segments:
                yield STTSegment(
                    text=(segment.text or "").strip(),
                    start_seconds=float(segment.start or 0.0),
                    end_seconds=float(segment.end or 0.0),
                    is_final=True,
                )

    def transcribe_file(self, audio_path: str, *, language: str | None = None) -> list[STTSegment]:
        model = self._ensure_loaded()
        if model is None:
            return []
        segments, _info = model.transcribe(  # type: ignore[attr-defined]
            audio_path,
            language=language or self.language,
            beam_size=self.beam_size,
            vad_filter=True,
        )
        return [
            STTSegment(
                text=(segment.text or "").strip(),
                start_seconds=float(segment.start or 0.0),
                end_seconds=float(segment.end or 0.0),
                is_final=True,
            )
            for segment in segments
        ]


_singleton: StreamSTT | None = None
_singleton_lock = threading.Lock()


def get_stream_stt() -> StreamSTT:
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            return _singleton
        candidate = WhisperStreamSTT()
        _singleton = candidate if candidate.available else DummyStreamSTT()
        return _singleton
