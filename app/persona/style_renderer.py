from __future__ import annotations

import re

from app.persona.persona_schema import PersonaProfile, RelationshipOverlay


class StyleRenderer:
    def render(
        self,
        text: str,
        *,
        task_type: str,
        persona: PersonaProfile,
        overlay: RelationshipOverlay,
        user_input: str = "",
    ) -> str:
        cleaned = self._normalize_text(text)
        if not cleaned:
            return ""
        cleaned = self._limit_exclamations(cleaned, persona.anti_drift_guard.max_exclamation_marks)
        cleaned = self._limit_sentence_count(cleaned, task_type=task_type, overlay=overlay)
        return cleaned.strip()

    def _normalize_text(self, text: str) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        normalized = re.sub(r"[ \t]+", " ", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized

    def _limit_exclamations(self, text: str, max_marks: int) -> str:
        cap = max(1, max_marks)
        return re.sub(rf"[!！]{{{cap + 1},}}", "！" if "！" in text else "!", text)

    def _limit_sentence_count(self, text: str, *, task_type: str, overlay: RelationshipOverlay) -> str:
        if task_type == "chat" and overlay.verbosity not in {"very_low", "low"}:
            return text
        sentences = re.split(r"(?<=[。！？!?])\s*", text)
        sentences = [item.strip() for item in sentences if item.strip()]
        max_sentences = 6
        if len(sentences) <= max_sentences:
            return text
        return " ".join(sentences[:max_sentences]).strip()
