from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


class ProviderRequestError(RuntimeError):
    def __init__(self, kind: str, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


@dataclass(slots=True)
class ProviderConfig:
    provider_id: str
    provider_type: str
    display_name: str
    base_url: str
    model: str
    api_key_ref: str = ""
    api_key_env_name: str = ""
    api_style: str = "chat_completions"
    stream: bool = False
    timeout_seconds: int = 45
    max_retries: int = 1
    enabled: bool = True
    supports_tools: bool = False
    supports_vision: bool = False
    supports_reasoning: bool = False
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderRouteDecision:
    active_mode: str
    provider_id: str
    model: str
    route_type: str
    fallback_used: bool = False
    fallback_reason: str = ""
    stream: bool = False
    warning: str = ""


@dataclass(slots=True)
class ProviderHTTPResponse:
    ok: bool
    status_code: int
    text: str = ""
    json_data: dict[str, Any] | None = None
    error_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    _text_iter_factory: Callable[[], Iterable[str]] | None = None

    @classmethod
    def success(
        cls,
        *,
        text: str,
        json_data: dict[str, Any] | None = None,
        status_code: int = 200,
        metadata: dict[str, Any] | None = None,
        text_iter_factory: Callable[[], Iterable[str]] | None = None,
    ) -> "ProviderHTTPResponse":
        return cls(
            ok=True,
            status_code=status_code,
            text=text,
            json_data=json_data,
            metadata=dict(metadata or {}),
            _text_iter_factory=text_iter_factory,
        )

    @classmethod
    def error(
        cls,
        message: str,
        *,
        status_code: int = 500,
        metadata: dict[str, Any] | None = None,
    ) -> "ProviderHTTPResponse":
        return cls(
            ok=False,
            status_code=status_code,
            text="",
            json_data=None,
            error_message=message,
            metadata=dict(metadata or {}),
        )

    def json(self) -> dict[str, Any]:
        if self.json_data is not None:
            choices = self.json_data.get("choices") if isinstance(self.json_data, dict) else None
            if choices:
                return self.json_data
            return {
                "choices": [{"message": {"content": self.text}}],
                "_raw": self.json_data,
            }
        if self.ok:
            return {"choices": [{"message": {"content": self.text}}]}
        return {"error": {"message": self.error_message}}

    def iter_text(self) -> Iterable[str]:
        if self._text_iter_factory is not None:
            yield from self._text_iter_factory()
            return
        if self.text:
            yield self.text

    def iter_lines(self, decode_unicode: bool = True) -> Iterable[str]:
        for chunk in self.iter_text():
            yield f"data: {chunk}" if not decode_unicode else chunk

    def close(self) -> None:
        return
