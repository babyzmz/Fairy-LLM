from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID


class ProviderKind(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    LOCAL_OPENAI_COMPATIBLE = "local_openai_compatible"


class ProviderCapability(StrEnum):
    TEXT = "text"
    TOOLS = "tools"
    VISION = "vision"
    STRUCTURED_OUTPUT = "structured_output"
    STT = "stt"
    TTS = "tts"


class ProviderHealthStatus(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ModelRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ModelDeltaKind(StrEnum):
    TEXT = "text"
    TOOL_CALL = "tool_call"
    USAGE = "usage"
    DONE = "done"


class ProviderAttemptStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ProviderErrorCategory(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROTOCOL = "protocol"
    CONTEXT_LENGTH = "context_length"
    CONTENT_REJECTED = "content_rejected"
    NETWORK = "network"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderAttemptEvent:
    profile_id: str
    attempt_number: int
    status: ProviderAttemptStatus
    error_category: ProviderErrorCategory | None = None
    usage: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    id: str
    display_name: str
    kind: ProviderKind
    base_url: str
    model_id: str
    capabilities: frozenset[ProviderCapability]
    credential_ref: str | None
    fallback_profile_id: str | None
    timeout_seconds: float
    enabled: bool

    @classmethod
    def create(
        cls,
        *,
        profile_id: str,
        display_name: str,
        kind: ProviderKind,
        base_url: str,
        model_id: str,
        capabilities: frozenset[ProviderCapability],
        credential_ref: str | None,
        fallback_profile_id: str | None,
        timeout_seconds: float,
        enabled: bool,
    ) -> ProviderProfile:
        normalized_id = _required_text(profile_id, "profile_id", maximum=128)
        normalized_capabilities = frozenset(capabilities)
        if ProviderCapability.TEXT not in normalized_capabilities:
            raise ValueError("provider capabilities must include text")
        if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be between 0 and 300")
        fallback = _optional_text(
            fallback_profile_id,
            "fallback_profile_id",
            maximum=128,
        )
        if fallback == normalized_id:
            raise ValueError("fallback_profile_id cannot reference the same profile")
        return cls(
            id=normalized_id,
            display_name=_required_text(display_name, "display_name", maximum=255),
            kind=ProviderKind(kind),
            base_url=_normalize_base_url(base_url, ProviderKind(kind)),
            model_id=_required_text(model_id, "model_id", maximum=255),
            capabilities=normalized_capabilities,
            credential_ref=_optional_text(
                credential_ref,
                "credential_ref",
                maximum=255,
            ),
            fallback_profile_id=fallback,
            timeout_seconds=float(timeout_seconds),
            enabled=bool(enabled),
        )

    def public(self, *, credential_configured: bool) -> PublicProviderProfile:
        return PublicProviderProfile(
            id=self.id,
            display_name=self.display_name,
            kind=self.kind,
            base_url=self.base_url,
            model_id=self.model_id,
            capabilities=self.capabilities,
            fallback_profile_id=self.fallback_profile_id,
            timeout_seconds=self.timeout_seconds,
            enabled=self.enabled,
            credential_required=self.credential_ref is not None,
            credential_configured=credential_configured,
        )


@dataclass(frozen=True, slots=True)
class PublicProviderProfile:
    id: str
    display_name: str
    kind: ProviderKind
    base_url: str
    model_id: str
    capabilities: frozenset[ProviderCapability]
    fallback_profile_id: str | None
    timeout_seconds: float
    enabled: bool
    credential_required: bool
    credential_configured: bool


@dataclass(frozen=True, slots=True)
class ModelToolCall:
    id: str
    name: str
    arguments: str

    @classmethod
    def create(
        cls,
        *,
        tool_call_id: str,
        name: str,
        arguments: str,
    ) -> ModelToolCall:
        normalized_arguments = arguments.strip()
        if not normalized_arguments:
            raise ValueError("tool call arguments are required")
        return cls(
            id=_required_text(tool_call_id, "tool_call_id", maximum=255),
            name=_required_text(name, "tool name", maximum=128),
            arguments=normalized_arguments,
        )


@dataclass(frozen=True, slots=True)
class ModelImage:
    task_id: UUID
    media_type: str
    data: memoryview
    content_hash: str
    width: int
    height: int
    label: str
    untrusted_data: bool

    @classmethod
    def create(
        cls,
        *,
        task_id: UUID,
        media_type: str,
        data: memoryview,
        content_hash: str,
        width: int,
        height: int,
        label: str,
        untrusted_data: bool,
    ) -> ModelImage:
        if media_type != "image/png":
            raise ValueError("model image media type must be image/png")
        if data.nbytes < 1 or data.nbytes > 20 * 1024 * 1024:
            raise ValueError("model image data exceeds the byte limit")
        if len(content_hash) != 64 or any(
            character not in "0123456789abcdef" for character in content_hash
        ):
            raise ValueError("model image hash is invalid")
        if width < 1 or height < 1 or width * height > 33_177_600:
            raise ValueError("model image dimensions exceed the pixel limit")
        if label != "untrusted_screen_content" or untrusted_data is not True:
            raise ValueError("model images must be labelled as untrusted screen content")
        return cls(
            task_id=task_id,
            media_type=media_type,
            data=data,
            content_hash=content_hash,
            width=width,
            height=height,
            label=label,
            untrusted_data=True,
        )


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: ModelRole
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ModelToolCall, ...] = ()
    images: tuple[ModelImage, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        role: ModelRole,
        content: str,
        name: str | None = None,
        tool_call_id: str | None = None,
        tool_calls: tuple[ModelToolCall, ...] = (),
        images: tuple[ModelImage, ...] = (),
    ) -> ModelMessage:
        normalized = content.strip()
        normalized_calls = tuple(tool_calls)
        normalized_images = tuple(images)
        if not normalized and not (
            (ModelRole(role) is ModelRole.ASSISTANT and normalized_calls)
            or (ModelRole(role) is ModelRole.USER and normalized_images)
        ):
            raise ValueError("message content is required")
        if len(normalized) > 1_000_000:
            raise ValueError("message content is too large")
        if normalized_calls and ModelRole(role) is not ModelRole.ASSISTANT:
            raise ValueError("only assistant messages can contain tool_calls")
        if normalized_images and ModelRole(role) is not ModelRole.USER:
            raise ValueError("only user messages can contain images")
        if len(normalized_images) > 4:
            raise ValueError("a model message can contain at most four images")
        normalized_tool_call_id = _optional_text(
            tool_call_id,
            "tool_call_id",
            maximum=255,
        )
        if ModelRole(role) is ModelRole.TOOL and normalized_tool_call_id is None:
            raise ValueError("tool messages require tool_call_id")
        return cls(
            role=ModelRole(role),
            content=normalized,
            name=_optional_text(name, "name", maximum=128),
            tool_call_id=normalized_tool_call_id,
            tool_calls=normalized_calls,
            images=normalized_images,
        )


@dataclass(frozen=True, slots=True)
class ModelTool:
    name: str
    description: str
    input_schema: Mapping[str, Any]

    @classmethod
    def create(
        cls,
        *,
        name: str,
        description: str,
        input_schema: Mapping[str, Any],
    ) -> ModelTool:
        schema = dict(input_schema)
        if schema.get("type") != "object":
            raise ValueError("tool input_schema must describe an object")
        return cls(
            name=_required_text(name, "tool name", maximum=128),
            description=_required_text(
                description,
                "tool description",
                maximum=4_096,
            ),
            input_schema=MappingProxyType(schema),
        )


@dataclass(frozen=True, slots=True)
class ModelRequest:
    profile_id: str
    messages: tuple[ModelMessage, ...]
    tools: tuple[ModelTool, ...]
    required_capabilities: frozenset[ProviderCapability]
    max_output_tokens: int

    @classmethod
    def create(
        cls,
        *,
        profile_id: str,
        messages: tuple[ModelMessage, ...],
        tools: tuple[ModelTool, ...],
        required_capabilities: frozenset[ProviderCapability],
        max_output_tokens: int,
    ) -> ModelRequest:
        if not messages:
            raise ValueError("model request messages are required")
        capabilities = frozenset(required_capabilities)
        if ProviderCapability.TEXT not in capabilities:
            raise ValueError("model request capabilities must include text")
        if tools and ProviderCapability.TOOLS not in capabilities:
            raise ValueError("model request with tools requires tools capability")
        if any(message.images for message in messages) and (
            ProviderCapability.VISION not in capabilities
        ):
            raise ValueError("model request with images requires vision capability")
        if isinstance(max_output_tokens, bool) or not 1 <= max_output_tokens <= 131_072:
            raise ValueError("max_output_tokens is outside the supported range")
        return cls(
            profile_id=_required_text(profile_id, "profile_id", maximum=128),
            messages=tuple(messages),
            tools=tuple(tools),
            required_capabilities=capabilities,
            max_output_tokens=max_output_tokens,
        )

    def for_profile(self, profile_id: str) -> ModelRequest:
        return ModelRequest.create(
            profile_id=profile_id,
            messages=self.messages,
            tools=self.tools,
            required_capabilities=self.required_capabilities,
            max_output_tokens=self.max_output_tokens,
        )


class _TextDeltaAccessor:
    def __get__(
        self,
        instance: ModelDelta | None,
        owner: type[ModelDelta],
    ) -> Any:
        if instance is None:
            return owner._create_text
        return instance._text


@dataclass(frozen=True, slots=True)
class ModelDelta:
    profile_id: str
    sequence: int
    kind: ModelDeltaKind
    _text: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_arguments_fragment: str | None = None
    usage: Mapping[str, int] = field(default_factory=dict)
    finish_reason: str | None = None

    text = _TextDeltaAccessor()

    @classmethod
    def _create_text(
        cls,
        *,
        profile_id: str,
        sequence: int,
        text: str,
    ) -> ModelDelta:
        normalized = text
        if not normalized:
            raise ValueError("delta text is required")
        return cls(
            profile_id=_required_text(profile_id, "profile_id", maximum=128),
            sequence=_positive_sequence(sequence),
            kind=ModelDeltaKind.TEXT,
            _text=normalized,
        )

    @classmethod
    def tool_call(
        cls,
        *,
        profile_id: str,
        sequence: int,
        tool_call_id: str,
        tool_name: str | None,
        arguments_fragment: str,
    ) -> ModelDelta:
        return cls(
            profile_id=_required_text(profile_id, "profile_id", maximum=128),
            sequence=_positive_sequence(sequence),
            kind=ModelDeltaKind.TOOL_CALL,
            tool_call_id=_required_text(
                tool_call_id,
                "tool_call_id",
                maximum=255,
            ),
            tool_name=_optional_text(tool_name, "tool_name", maximum=128),
            tool_arguments_fragment=arguments_fragment,
        )

    @classmethod
    def usage_delta(
        cls,
        *,
        profile_id: str,
        sequence: int,
        usage: Mapping[str, int],
    ) -> ModelDelta:
        normalized = {
            str(key): int(value)
            for key, value in usage.items()
            if not isinstance(value, bool) and int(value) >= 0
        }
        return cls(
            profile_id=_required_text(profile_id, "profile_id", maximum=128),
            sequence=_positive_sequence(sequence),
            kind=ModelDeltaKind.USAGE,
            usage=MappingProxyType(normalized),
        )

    @classmethod
    def done(
        cls,
        *,
        profile_id: str,
        sequence: int,
        finish_reason: str | None,
    ) -> ModelDelta:
        return cls(
            profile_id=_required_text(profile_id, "profile_id", maximum=128),
            sequence=_positive_sequence(sequence),
            kind=ModelDeltaKind.DONE,
            finish_reason=_optional_text(
                finish_reason,
                "finish_reason",
                maximum=128,
            ),
        )


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    profile_id: str
    status: ProviderHealthStatus
    error_code: str | None
    diagnostics: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        profile_id: str,
        status: ProviderHealthStatus,
        error_code: str | None,
        diagnostics: tuple[str, ...],
    ) -> ProviderHealth:
        return cls(
            profile_id=_required_text(profile_id, "profile_id", maximum=128),
            status=ProviderHealthStatus(status),
            error_code=_optional_text(error_code, "error_code", maximum=128),
            diagnostics=tuple(
                _required_text(value, "diagnostic", maximum=1_024) for value in diagnostics
            ),
        )


def _normalize_base_url(value: str, kind: ProviderKind) -> str:
    normalized = _required_text(value, "base_url", maximum=2_048).rstrip("/")
    parsed = urlsplit(normalized)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("base_url must not contain credentials, query, or fragment")
    if kind is ProviderKind.OPENAI_COMPATIBLE:
        if parsed.scheme != "https":
            raise ValueError("cloud provider base_url must use https")
    elif parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}:
        raise ValueError("local provider base_url must use literal loopback http")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _required_text(value: str, field_name: str, *, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} is too long")
    return normalized


def _optional_text(
    value: str | None,
    field_name: str,
    *,
    maximum: int,
) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name, maximum=maximum)


def _positive_sequence(value: int) -> int:
    if isinstance(value, bool) or value < 1:
        raise ValueError("delta sequence must be positive")
    return value
