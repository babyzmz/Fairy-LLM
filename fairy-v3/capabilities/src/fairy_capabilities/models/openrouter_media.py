from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from fairy_core.media.ports import (
    GeneratedMedia,
    ImageGenerationRequest,
    MediaProviderVideoStatus,
    MusicGenerationRequest,
    VideoGenerationRequest,
    VideoProviderJob,
)
from fairy_core.providers import (
    CancellationToken,
    ProviderAuthenticationError,
    ProviderContentRejectedError,
    ProviderNetworkError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    SecretValue,
)

_MAX_IMAGE_BYTES = 64 * 1024 * 1024
_MAX_AUDIO_BYTES = 512 * 1024 * 1024
_MAX_VIDEO_BYTES = 512 * 1024 * 1024


class OpenRouterMediaProvider:
    def __init__(
        self,
        *,
        base_url: str,
        secret: SecretValue | None,
        client: httpx.Client | None = None,
        timeout_seconds: float = 120,
    ) -> None:
        normalized_url = base_url.rstrip("/")
        parsed = urlsplit(normalized_url)
        if parsed.scheme != "https" or parsed.hostname != "openrouter.ai":
            raise ValueError("OpenRouter media must use the official HTTPS endpoint")
        if not 0 < timeout_seconds <= 900:
            raise ValueError("OpenRouter media timeout must be between 0 and 900 seconds")
        self._base_url = normalized_url
        self._secret = secret
        self._timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.Client(
            trust_env=False,
            follow_redirects=False,
            timeout=timeout_seconds,
        )

    @property
    def account_id(self) -> str:
        return "openrouter-default"

    @property
    def credential_configured(self) -> bool:
        return self._secret is not None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def generate_image(
        self,
        request: ImageGenerationRequest,
        cancellation: CancellationToken,
    ) -> GeneratedMedia:
        self._require_credential()
        cancellation.raise_if_cancelled()
        payload: dict[str, Any] = {
            "model": request.model_id,
            "prompt": request.prompt,
            "size": request.size,
            "aspect_ratio": request.aspect_ratio,
            "n": 1,
            "response_format": "b64_json",
            "provider": self._provider_preferences(request.zero_data_retention),
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        response = self._request(
            "POST",
            "/images",
            cancellation=cancellation,
            json_payload=payload,
            idempotency_key=request.idempotency_key,
        )
        body = _json_object(response)
        data = body.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise ProviderProtocolError("image response must contain exactly one result")
        encoded = data[0].get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise ProviderProtocolError("image response did not contain inline image data")
        content = _decode_base64(encoded, maximum=_MAX_IMAGE_BYTES, label="image")
        return GeneratedMedia(
            content=content,
            media_type=_sniff_image_type(content),
            usage_cost=_usage_cost(body.get("usage")),
        )

    def generate_music(
        self,
        request: MusicGenerationRequest,
        cancellation: CancellationToken,
    ) -> GeneratedMedia:
        self._require_credential()
        cancellation.raise_if_cancelled()
        payload: dict[str, Any] = {
            "model": request.model_id,
            "messages": [{"role": "user", "content": request.prompt}],
            "modalities": ["text", "audio"],
            "audio": {"format": request.output_format},
            "stream": True,
            "provider": self._provider_preferences(request.zero_data_retention),
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        audio = bytearray()
        transcript: list[str] = []
        usage_cost: str | None = None
        try:
            with self._client.stream(
                "POST",
                self._url("/chat/completions"),
                headers=self._headers(request.idempotency_key),
                json=payload,
                timeout=self._timeout_seconds,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise _response_error(response)
                for line in response.iter_lines():
                    cancellation.raise_if_cancelled()
                    frame = _sse_frame(line)
                    if frame is None:
                        continue
                    if frame == "[DONE]":
                        break
                    body = _json_text(frame)
                    if body.get("error") is not None:
                        raise ProviderProtocolError("music stream reported an upstream error")
                    for choice in _choices(body):
                        delta = choice.get("delta") or {}
                        if not isinstance(delta, dict):
                            raise ProviderProtocolError("music stream delta must be an object")
                        raw_audio = delta.get("audio")
                        if raw_audio is not None:
                            if not isinstance(raw_audio, dict):
                                raise ProviderProtocolError(
                                    "music stream audio delta must be an object"
                                )
                            encoded = raw_audio.get("data")
                            if encoded is not None:
                                if not isinstance(encoded, str):
                                    raise ProviderProtocolError(
                                        "music stream audio data must be base64 text"
                                    )
                                chunk = _decode_base64(
                                    encoded,
                                    maximum=_MAX_AUDIO_BYTES,
                                    label="music chunk",
                                )
                                if len(audio) + len(chunk) > _MAX_AUDIO_BYTES:
                                    raise ProviderProtocolError("music output exceeds size limit")
                                audio.extend(chunk)
                            raw_transcript = raw_audio.get("transcript")
                            if isinstance(raw_transcript, str) and raw_transcript:
                                transcript.append(raw_transcript)
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            transcript.append(content)
                    parsed_cost = _usage_cost(body.get("usage"))
                    if parsed_cost is not None:
                        usage_cost = parsed_cost
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError("music generation timed out") from error
        except httpx.HTTPError as error:
            raise ProviderNetworkError("music generation transport failed") from error
        cancellation.raise_if_cancelled()
        if not audio:
            raise ProviderProtocolError("music stream completed without audio data")
        return GeneratedMedia(
            content=bytes(audio),
            media_type="audio/wav",
            usage_cost=usage_cost,
            transcript="".join(transcript).strip() or None,
        )

    def start_video(
        self,
        request: VideoGenerationRequest,
        cancellation: CancellationToken,
    ) -> VideoProviderJob:
        self._require_credential()
        if request.zero_data_retention:
            raise ProviderUnavailableError("OpenRouter video generation does not support ZDR")
        payload: dict[str, Any] = {
            "model": request.model_id,
            "prompt": request.prompt,
            "duration": request.duration_seconds,
            "resolution": request.resolution,
            "aspect_ratio": request.aspect_ratio,
            "generate_audio": request.generate_audio,
            "provider": self._provider_preferences(False),
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        response = self._request(
            "POST",
            "/videos",
            cancellation=cancellation,
            json_payload=payload,
            idempotency_key=request.idempotency_key,
            accepted_statuses=frozenset({200, 201, 202}),
        )
        return _video_job(_json_object(response))

    def get_video(
        self,
        provider_job_id: str,
        cancellation: CancellationToken,
    ) -> VideoProviderJob:
        response = self._request(
            "GET",
            _video_path(provider_job_id),
            cancellation=cancellation,
        )
        return _video_job(_json_object(response), expected_id=provider_job_id)

    def download_video(
        self,
        provider_job_id: str,
        cancellation: CancellationToken,
    ) -> GeneratedMedia:
        response = self._request(
            "GET",
            f"{_video_path(provider_job_id)}/content?index=0",
            cancellation=cancellation,
        )
        content_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
        if content_type not in {"video/mp4", "video/webm", "application/octet-stream"}:
            raise ProviderProtocolError("video response media type is unsupported")
        if len(response.content) > _MAX_VIDEO_BYTES:
            raise ProviderProtocolError("video output exceeds size limit")
        if not response.content:
            raise ProviderProtocolError("video response is empty")
        return GeneratedMedia(
            content=response.content,
            media_type=(
                "video/mp4" if content_type == "application/octet-stream" else content_type
            ),
            usage_cost=_usage_cost_header(response.headers.get("x-openrouter-cost")),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        cancellation: CancellationToken,
        json_payload: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        accepted_statuses: frozenset[int] | None = None,
    ) -> httpx.Response:
        self._require_credential()
        cancellation.raise_if_cancelled()
        try:
            response = self._client.request(
                method,
                self._url(path),
                headers=self._headers(idempotency_key),
                json=dict(json_payload) if json_payload is not None else None,
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError("media request timed out") from error
        except httpx.HTTPError as error:
            raise ProviderNetworkError("media transport failed") from error
        cancellation.raise_if_cancelled()
        statuses = accepted_statuses or frozenset(range(200, 300))
        if response.status_code not in statuses:
            raise _response_error(response)
        return response

    def _url(self, path: str) -> str:
        url = f"{self._base_url}/{path.lstrip('/')}"
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "openrouter.ai":
            raise ProviderProtocolError("media destination is not trusted")
        return url

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        assert self._secret is not None
        headers = {
            "Authorization": f"Bearer {self._secret.reveal()}",
            "Accept": "application/json, text/event-stream, video/mp4, video/webm",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://fairy.local",
            "X-Title": "Fairy",
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def _provider_preferences(zero_data_retention: bool) -> dict[str, Any]:
        preferences: dict[str, Any] = {
            "allow_fallbacks": True,
            "require_parameters": True,
            "data_collection": "deny",
        }
        if zero_data_retention:
            preferences["zdr"] = True
        return preferences

    def _require_credential(self) -> None:
        if self._secret is None:
            raise ProviderUnavailableError("OpenRouter media credential is not configured")


def _video_path(provider_job_id: str) -> str:
    normalized = provider_job_id.strip()
    if not normalized or len(normalized) > 255:
        raise ValueError("video provider job id is invalid")
    return f"/videos/{quote(normalized, safe='')}"


def _video_job(
    payload: Mapping[str, Any],
    *,
    expected_id: str | None = None,
) -> VideoProviderJob:
    raw = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    assert isinstance(raw, Mapping)
    provider_job_id = raw.get("id")
    if not isinstance(provider_job_id, str) or not provider_job_id:
        raise ProviderProtocolError("video response is missing its job id")
    if expected_id is not None and provider_job_id != expected_id:
        raise ProviderProtocolError("video response job identity changed")
    raw_status = raw.get("status")
    try:
        status = MediaProviderVideoStatus(str(raw_status).casefold())
    except ValueError as error:
        raise ProviderProtocolError("video response status is invalid") from error
    error_code = None
    if status in {MediaProviderVideoStatus.FAILED, MediaProviderVideoStatus.EXPIRED}:
        error_code = _safe_provider_code(raw.get("error"))
    return VideoProviderJob(
        provider_job_id=provider_job_id,
        status=status,
        usage_cost=_usage_cost(raw.get("usage")),
        error_code=error_code,
    )


def _json_object(response: httpx.Response) -> Mapping[str, Any]:
    try:
        payload = response.json()
    except ValueError as error:
        raise ProviderProtocolError("media response is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ProviderProtocolError("media response must be an object")
    return payload


def _json_text(value: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise ProviderProtocolError("media stream contains malformed JSON") from error
    if not isinstance(payload, dict):
        raise ProviderProtocolError("media stream frame must be an object")
    return payload


def _sse_frame(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(":"):
        return None
    if not stripped.startswith("data:"):
        return None
    return stripped[5:].strip()


def _choices(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = payload.get("choices", [])
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ProviderProtocolError("media stream choices must be an array")
    return tuple(raw)


def _decode_base64(value: str, *, maximum: int, label: str) -> bytes:
    if len(value) > ((maximum + 2) // 3) * 4 + 8:
        raise ProviderProtocolError(f"{label} exceeds size limit")
    try:
        content = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ProviderProtocolError(f"{label} is not valid base64") from error
    if not content or len(content) > maximum:
        raise ProviderProtocolError(f"{label} has an invalid size")
    return content


def _sniff_image_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    raise ProviderProtocolError("image output has an unsupported signature")


def _usage_cost(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("cost") or value.get("total_cost")
    return _usage_cost_header(value)


def _usage_cost_header(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return format(amount.normalize(), "f")


def _safe_provider_code(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("code")
    if not isinstance(value, str):
        return "MEDIA_GENERATION_FAILED"
    normalized = "".join(
        character for character in value.upper() if character.isalnum() or character == "_"
    )
    return normalized[:128] or "MEDIA_GENERATION_FAILED"


def _response_error(response: httpx.Response) -> Exception:
    if response.status_code in {401, 403}:
        return ProviderAuthenticationError("media provider authentication failed")
    if response.status_code == 429:
        return ProviderRateLimitError("media provider rate limit exceeded")
    if response.status_code in {408, 504}:
        return ProviderTimeoutError("media provider request timed out")
    code = _safe_error_code(response)
    if code in {"content_filter", "content_policy_violation", "moderation_blocked"}:
        return ProviderContentRejectedError("media provider rejected the requested content")
    if response.status_code >= 500:
        return ProviderNetworkError("media provider upstream is unavailable")
    return ProviderProtocolError("media provider rejected the request")


def _safe_error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    return None


__all__ = ["OpenRouterMediaProvider"]
