from __future__ import annotations

from dataclasses import asdict, dataclass, field
from time import time
from typing import Any, Literal


SpeechMode = Literal["concise_structured", "summary_first", "detailed_explainer"]
ResponseModality = Literal["text_only", "text_plus_card", "card_primary_text_summary"]
CardLayoutMode = Literal["single", "grid", "masonry"]
StreamEventType = Literal["message_start", "progress", "text_delta", "card", "message_end", "error"]


@dataclass(slots=True)
class ResponseRequestPlan:
    intent: str
    modality: ResponseModality
    speech_mode: SpeechMode
    allow_voice_streaming: bool = False
    planner_confidence: float = 0.0
    force_card_type: str = ""
    context_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResponseProgressEvent:
    stage: str
    text: str


@dataclass(slots=True)
class SpeechPayload:
    mode: SpeechMode
    text: str
    allow_streaming: bool = False


@dataclass(slots=True, init=False)
class CardPayload:
    type: str
    version: str
    data: dict[str, Any]
    layout: CardLayoutMode
    metadata: dict[str, Any]

    def __init__(
        self,
        *,
        type: str | None = None,
        version: str = "1",
        data: dict[str, Any] | None = None,
        layout: CardLayoutMode = "single",
        metadata: dict[str, Any] | None = None,
        card_type: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        resolved_type = str(type or card_type or "generic_info").strip().lower()
        self.type = resolved_type or "generic_info"
        self.version = str(version or "1").strip() or "1"
        self.data = dict(data or payload or {})
        self.layout = layout
        self.metadata = dict(metadata or {})

    @property
    def card_type(self) -> str:
        return self.type

    @property
    def payload(self) -> dict[str, Any]:
        return self.data

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "version": self.version,
            "data": dict(self.data),
            "layout": self.layout,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class NormalizedAssistantResponse:
    intent: str
    modality: ResponseModality
    text_reply: str
    text_rich_html: str = ""
    request_id: str = ""
    session_id: str = ""
    speech_payload: SpeechPayload = field(
        default_factory=lambda: SpeechPayload(mode="detailed_explainer", text="", allow_streaming=False)
    )
    card_payloads: list[CardPayload] = field(default_factory=list)
    progress_events: list[ResponseProgressEvent] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def mode(self) -> ResponseModality:
        return self.modality

    @property
    def text(self) -> str:
        return self.text_reply

    @property
    def cards(self) -> list[dict[str, Any]]:
        return [card.to_dict() for card in self.card_payloads]

    def to_contract_dict(self) -> dict[str, Any]:
        meta = dict(self.meta)
        meta.setdefault("intent", self.intent)
        meta.setdefault("modality", self.modality)
        meta.setdefault(
            "speech",
            {
                "mode": self.speech_payload.mode,
                "text": self.speech_payload.text,
                "allow_streaming": self.speech_payload.allow_streaming,
            },
        )
        if self.progress_events:
            meta.setdefault(
                "progress_events",
                [{"stage": event.stage, "text": event.text} for event in self.progress_events],
            )
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "text": self.text_reply,
            "cards": self.cards,
            "meta": meta,
            "errors": list(self.errors),
        }


@dataclass(slots=True)
class StreamEventBase:
    event: StreamEventType
    request_id: str
    session_id: str
    sequence: int = 0
    timestamp_ms: int = field(default_factory=lambda: int(time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MessageStartEvent(StreamEventBase):
    meta: dict[str, Any] = field(default_factory=dict)

    def __init__(self, *, request_id: str, session_id: str, meta: dict[str, Any] | None = None) -> None:
        StreamEventBase.__init__(self, event="message_start", request_id=request_id, session_id=session_id)
        self.meta = dict(meta or {})


@dataclass(slots=True)
class ProgressStreamEvent(StreamEventBase):
    stage: str = ""
    text: str = ""

    def __init__(self, *, request_id: str, session_id: str, stage: str, text: str) -> None:
        StreamEventBase.__init__(self, event="progress", request_id=request_id, session_id=session_id)
        self.stage = str(stage or "").strip()
        self.text = str(text or "").strip()


@dataclass(slots=True)
class TextDeltaEvent(StreamEventBase):
    text: str = ""

    def __init__(self, *, request_id: str, session_id: str, text: str) -> None:
        StreamEventBase.__init__(self, event="text_delta", request_id=request_id, session_id=session_id)
        self.text = str(text or "")


@dataclass(slots=True)
class CardStreamEvent(StreamEventBase):
    card: dict[str, Any] = field(default_factory=dict)

    def __init__(self, *, request_id: str, session_id: str, card: dict[str, Any]) -> None:
        StreamEventBase.__init__(self, event="card", request_id=request_id, session_id=session_id)
        self.card = dict(card or {})


@dataclass(slots=True)
class MessageEndEvent(StreamEventBase):
    text: str = ""
    cards: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def __init__(
        self,
        *,
        request_id: str,
        session_id: str,
        text: str = "",
        cards: list[dict[str, Any]] | None = None,
        meta: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        StreamEventBase.__init__(self, event="message_end", request_id=request_id, session_id=session_id)
        self.text = str(text or "")
        self.cards = list(cards or [])
        self.meta = dict(meta or {})
        self.errors = list(errors or [])


@dataclass(slots=True)
class ErrorEvent(StreamEventBase):
    code: str = "runtime_error"
    message: str = ""

    def __init__(self, *, request_id: str, session_id: str, code: str, message: str) -> None:
        StreamEventBase.__init__(self, event="error", request_id=request_id, session_id=session_id)
        self.code = str(code or "runtime_error")
        self.message = str(message or "")
