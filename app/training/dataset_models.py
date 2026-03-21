from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


CATEGORY_CHOICES = [
    "",
    "system",
    "analysis",
    "instruction",
    "sarcasm",
    "observation",
    "greeting",
    "calm_response",
    "long_reasoning",
]

LENGTH_CHOICES = ["", "short", "medium", "long"]

EMOTION_CHOICES = ["", "calm", "neutral", "cold", "slight_sarcasm", "confidence"]

STATUS_CHOICES = [
    "parsed",
    "tagged",
    "queued",
    "generating",
    "generated",
    "failed",
    "skipped",
]


@dataclass(slots=True)
class TrainingEntry:
    id: int
    text: str
    category: str | None = None
    length_type: str | None = None
    emotion: str | None = None
    enabled: bool = True
    audio_path: str | None = None
    status: str = "parsed"
    note: str = ""
    error_message: str = ""
    generated_at: str | None = None
    voice_provider: str | None = None
    source_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TrainingEntry":
        return cls(
            id=int(payload.get("id", 0) or 0),
            text=str(payload.get("text", "")).strip(),
            category=_clean_optional(payload.get("category")),
            length_type=_clean_optional(payload.get("length_type")),
            emotion=_clean_optional(payload.get("emotion")),
            enabled=bool(payload.get("enabled", True)),
            audio_path=_clean_optional(payload.get("audio_path")),
            status=str(payload.get("status", "parsed") or "parsed"),
            note=str(payload.get("note", "") or ""),
            error_message=str(payload.get("error_message", "") or ""),
            generated_at=_clean_optional(payload.get("generated_at")),
            voice_provider=_clean_optional(payload.get("voice_provider")),
            source_index=payload.get("source_index"),
        )

    def touch_generated(self, *, audio_path: str, voice_provider: str) -> None:
        self.audio_path = audio_path
        self.voice_provider = voice_provider
        self.generated_at = datetime.now().isoformat(timespec="seconds")
        self.error_message = ""
        self.status = "generated"


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
