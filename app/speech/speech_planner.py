from __future__ import annotations

from dataclasses import dataclass

from app.response.models import NormalizedAssistantResponse
from app.speech.speech_mode import SpeechMode


@dataclass(slots=True)
class SpeechDecision:
    mode: SpeechMode
    text: str
    allow_streaming: bool = False
    reason: str = ""


class SpeechPlanner:
    def plan(self, response: NormalizedAssistantResponse) -> SpeechDecision:
        payload = response.speech_payload
        mode: SpeechMode = payload.mode if payload.mode in {"concise_structured", "summary_first", "detailed_explainer"} else "silent"
        text = str(payload.text or "").strip()
        if not text:
            return SpeechDecision(mode="silent", text="", allow_streaming=False, reason="empty_speech")
        if response.intent in {"weather", "location", "news"}:
            return SpeechDecision(mode=mode if mode != "detailed_explainer" else "summary_first", text=text, allow_streaming=False, reason="structured_response")
        return SpeechDecision(mode=mode, text=text, allow_streaming=bool(payload.allow_streaming and mode == "detailed_explainer"), reason="text_response")
