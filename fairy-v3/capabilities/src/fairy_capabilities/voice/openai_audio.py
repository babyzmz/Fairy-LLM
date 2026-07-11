from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
from fairy_core.providers import (
    CancellationToken,
    ProviderProfile,
    ProviderProtocolError,
    ProviderUnavailableError,
    SecretValue,
)
from fairy_core.voice import (
    AudioMediaType,
    SynthesizedAudio,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptSegment,
    VoiceSynthesisRequest,
    parse_pcm_wav,
)

_EXTENSIONS = {
    AudioMediaType.WEBM: "webm",
    AudioMediaType.WAV: "wav",
    AudioMediaType.MPEG: "mp3",
    AudioMediaType.MP4: "m4a",
    AudioMediaType.OGG: "ogg",
}


class OpenAIAudioAdapter:
    def __init__(
        self,
        *,
        profile: ProviderProfile,
        secret: SecretValue | None,
        client: httpx.Client | None = None,
    ) -> None:
        self.profile = profile
        self._secret = secret
        self._owns_client = client is None
        self._client = client or httpx.Client(
            trust_env=False,
            follow_redirects=False,
            timeout=profile.timeout_seconds,
        )

    @property
    def credential_configured(self) -> bool:
        return self.profile.credential_ref is None or self._secret is not None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def transcribe(
        self,
        request: TranscriptionRequest,
        cancellation: CancellationToken,
    ) -> TranscriptionResult:
        cancellation.raise_if_cancelled()
        self._validate_request_profile(request.profile_id)
        data = {
            "model": self.profile.model_id,
            "response_format": "verbose_json",
        }
        if request.language is not None:
            data["language"] = request.language
        extension = _EXTENSIONS[request.media_type]
        try:
            response = self._client.post(
                f"{self.profile.base_url}/audio/transcriptions",
                headers=self._headers(accept="application/json"),
                data=data,
                files={
                    "file": (
                        f"recording.{extension}",
                        request.audio,
                        request.media_type.value,
                    )
                },
                timeout=self.profile.timeout_seconds,
            )
            cancellation.raise_if_cancelled()
            self._require_success(response)
            payload = _json_object(response)
            result = _transcription_result(self.profile.id, payload)
            cancellation.raise_if_cancelled()
            return result
        except (ProviderProtocolError, ProviderUnavailableError):
            raise
        except httpx.TimeoutException as error:
            raise ProviderUnavailableError("voice transcription request timed out") from error
        except httpx.HTTPError as error:
            raise ProviderUnavailableError("voice transcription transport failed") from error

    def synthesize(
        self,
        request: VoiceSynthesisRequest,
        cancellation: CancellationToken,
    ) -> SynthesizedAudio:
        cancellation.raise_if_cancelled()
        self._validate_request_profile(request.profile_id)
        try:
            response = self._client.post(
                f"{self.profile.base_url}/audio/speech",
                headers=self._headers(accept="audio/wav"),
                json={
                    "model": self.profile.model_id,
                    "voice": request.voice,
                    "input": request.text,
                    "response_format": "wav",
                },
                timeout=self.profile.timeout_seconds,
            )
            cancellation.raise_if_cancelled()
            self._require_success(response)
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if media_type not in {"audio/wav", "audio/wave", "audio/x-wav"}:
                raise ProviderProtocolError("provider speech media type must be audio/wav")
            try:
                sample_rate, channels, frames = parse_pcm_wav(response.content)
                result = SynthesizedAudio.create(
                    profile_id=self.profile.id,
                    wav=response.content,
                    sample_rate=sample_rate,
                    channels=channels,
                    frames=frames,
                )
            except ValueError as error:
                raise ProviderProtocolError(str(error)) from error
            cancellation.raise_if_cancelled()
            return result
        except (ProviderProtocolError, ProviderUnavailableError):
            raise
        except httpx.TimeoutException as error:
            raise ProviderUnavailableError("voice synthesis request timed out") from error
        except httpx.HTTPError as error:
            raise ProviderUnavailableError("voice synthesis transport failed") from error

    def _validate_request_profile(self, profile_id: str) -> None:
        if profile_id != self.profile.id:
            raise ValueError("voice request profile does not match provider")
        if not self.credential_configured:
            raise ProviderUnavailableError("voice provider credential is not configured")

    def _headers(self, *, accept: str) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": "Fairy-V3/0.1",
        }
        if self._secret is not None:
            headers["Authorization"] = f"Bearer {self._secret.reveal()}"
        return headers

    @staticmethod
    def _require_success(response: httpx.Response) -> None:
        if 200 <= response.status_code < 300:
            return
        raise ProviderUnavailableError(f"voice provider request failed ({response.status_code})")


def _json_object(response: httpx.Response) -> Mapping[str, Any]:
    try:
        payload = response.json()
    except ValueError as error:
        raise ProviderProtocolError("provider returned malformed transcription JSON") from error
    if not isinstance(payload, dict):
        raise ProviderProtocolError("provider transcription response must be an object")
    return payload


def _transcription_result(
    profile_id: str,
    payload: Mapping[str, Any],
) -> TranscriptionResult:
    text = payload.get("text")
    language = payload.get("language")
    raw_segments = payload.get("segments", [])
    if not isinstance(text, str):
        raise ProviderProtocolError("provider transcript text must be a string")
    if language is not None and not isinstance(language, str):
        raise ProviderProtocolError("provider transcript language must be a string")
    if not isinstance(raw_segments, list):
        raise ProviderProtocolError("provider transcript segments must be a list")
    segments: list[TranscriptSegment] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            raise ProviderProtocolError("provider transcript segment must be an object")
        index = raw.get("id")
        segment_text = raw.get("text")
        start = raw.get("start")
        end = raw.get("end")
        if isinstance(index, bool) or not isinstance(index, int):
            raise ProviderProtocolError("provider transcript segment id must be an integer")
        if not isinstance(segment_text, str):
            raise ProviderProtocolError("provider transcript segment text must be a string")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
        ):
            raise ProviderProtocolError("provider transcript segment timing is invalid")
        try:
            segments.append(
                TranscriptSegment(
                    index=index,
                    text=segment_text,
                    start_seconds=float(start),
                    end_seconds=float(end),
                )
            )
        except ValueError as error:
            raise ProviderProtocolError(str(error)) from error
    try:
        return TranscriptionResult.create(
            profile_id=profile_id,
            text=text,
            language=language,
            segments=tuple(segments),
        )
    except ValueError as error:
        raise ProviderProtocolError(str(error)) from error


__all__ = ["OpenAIAudioAdapter"]
