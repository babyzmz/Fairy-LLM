from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ApiErrorModel(BaseModel):
    code: str = "runtime_error"
    message: str


class CardModel(BaseModel):
    type: str
    version: str = "1"
    data: dict[str, Any] = Field(default_factory=dict)
    layout: str = "single"
    metadata: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    service: str


class CapabilitiesResponse(BaseModel):
    chat: bool
    streaming: bool
    voice_input: bool
    voice_output: bool
    cards: list[str] = Field(default_factory=list)
    system_actions: list[str] = Field(default_factory=list)


class ChatInvokeRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str = Field(default="default", min_length=1)
    attachments: list[str] | None = None


class ChatInvokeResponse(BaseModel):
    request_id: str = ""
    session_id: str = "default"
    text: str = ""
    cards: list[CardModel] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    errors: list[ApiErrorModel] = Field(default_factory=list)


class StreamEventBase(BaseModel):
    event: str
    request_id: str
    session_id: str
    sequence: int = 0
    timestamp_ms: int = 0


class MessageStartEventModel(StreamEventBase):
    meta: dict[str, Any] = Field(default_factory=dict)


class ProgressEventModel(StreamEventBase):
    stage: str
    text: str


class TextDeltaEventModel(StreamEventBase):
    text: str


class CardEventModel(StreamEventBase):
    card: CardModel


class MessageEndEventModel(StreamEventBase):
    text: str = ""
    cards: list[CardModel] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    errors: list[ApiErrorModel] = Field(default_factory=list)


class ErrorEventModel(StreamEventBase):
    code: str
    message: str


class SystemEventModel(BaseModel):
    event: str
    timestamp_ms: int
    request_id: str | None = None
    session_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class SystemStateResponse(BaseModel):
    backend_status: str
    current_state: str = "booting"
    active_session: str | None = None
    active_stream_request: str | None = None
    is_streaming: bool = False
    last_error: str | None = None
    fairy: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    runtime_state_trace: list[dict[str, Any]] = Field(default_factory=list)
    recent_events: list[SystemEventModel] = Field(default_factory=list)


class SystemActionRequest(BaseModel):
    action: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class SystemActionResponse(BaseModel):
    action: str
    ok: bool
    message: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class VoiceSynthesizeRequest(BaseModel):
    text: str = Field(min_length=1)
    system_voice: bool = False


class VoiceSynthesizeResponse(BaseModel):
    audio_base64: str
    mime_type: str = "audio/wav"
