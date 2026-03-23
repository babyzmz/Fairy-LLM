from __future__ import annotations

import html
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ChatMessage:
    id: str
    role: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def create(
        cls,
        *,
        role: str,
        message_type: str,
        payload: dict[str, Any] | None = None,
        message_id: str | None = None,
        timestamp: float | None = None,
    ) -> "ChatMessage":
        return cls(
            id=message_id or uuid.uuid4().hex,
            role=(role or "assistant").strip().lower(),
            type=(message_type or "text").strip().lower(),
            payload=dict(payload or {}),
            timestamp=time.time() if timestamp is None else float(timestamp),
        )

    @classmethod
    def from_legacy(
        cls,
        speaker: str,
        text: str,
        *,
        rich_text: bool = False,
        message_id: str | None = None,
        timestamp: float | None = None,
    ) -> "ChatMessage":
        lowered = (speaker or "").strip().lower()
        if lowered in {"fairy", "assistant", "ai"}:
            role = "assistant"
        elif lowered in {"system"}:
            role = "system"
        else:
            role = "user"
        payload = {
            "speaker": speaker or role.title(),
            "text": text,
            "rich_text": bool(rich_text),
        }
        return cls.create(
            role=role,
            message_type="text",
            payload=payload,
            message_id=message_id,
            timestamp=timestamp,
        )

    def text_preview(self) -> str:
        text = str(self.payload.get("text", "") or "").strip()
        if text:
            return text
        if self.type == "weather":
            city = str(self.payload.get("city", "") or "").strip()
            temp = str(self.payload.get("temperature_c", "") or self.payload.get("temp", "") or "").strip()
            return f"{city} {temp}".strip()
        if self.type == "map":
            return str(self.payload.get("title", "") or self.payload.get("address", "") or "").strip()
        if self.type == "image":
            return str(self.payload.get("title", "") or self.payload.get("caption", "") or "").strip()
        if self.type == "news":
            items = self.payload.get("items", [])
            if isinstance(items, list) and items:
                first = items[0]
                if isinstance(first, dict):
                    return str(first.get("headline", "") or first.get("title", "") or "").strip()
        if self.type == "link":
            return str(self.payload.get("title", "") or self.payload.get("url", "") or "").strip()
        if self.type == "generic_info":
            return str(self.payload.get("title", "") or self.payload.get("summary", "") or self.payload.get("text", "") or "").strip()
        if self.type == "suggestion":
            return str(self.payload.get("body", "") or self.payload.get("text", "") or "").strip()
        return html.escape(str(self.payload))
