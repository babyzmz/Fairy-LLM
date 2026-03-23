from __future__ import annotations

from app.speech.speech_planner import SpeechDecision


class SpeechRouter:
    def route(self, decision: SpeechDecision) -> str:
        if decision.mode == "silent" or not decision.text:
            return "skip"
        if decision.allow_streaming:
            return "stream"
        return "queue"
