from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.response.models import CardLayoutMode


@dataclass(slots=True)
class ResolvedResponseLayout:
    mode: CardLayoutMode
    reason: str


class ResponseCardLayoutPolicy:
    def declared_types(self) -> set[str]:
        return {"weather", "location", "news_list", "generic_info"}

    def resolve(self, card_type: str, data: dict[str, Any]) -> ResolvedResponseLayout:
        normalized_type = str(card_type or "generic_info").strip().lower()
        if normalized_type == "news_list":
            return ResolvedResponseLayout(mode="masonry", reason="news_list prefers vertical masonry scanning")
        if normalized_type == "generic_info":
            fields = data.get("fields")
            if isinstance(fields, list) and len(fields) >= 6:
                return ResolvedResponseLayout(mode="grid", reason="generic_info contains many compact fields")
            return ResolvedResponseLayout(mode="single", reason="generic_info defaults to a single summary card")
        if normalized_type in {"weather", "location"}:
            return ResolvedResponseLayout(mode="single", reason=f"{normalized_type} renders as a focused single card")
        return ResolvedResponseLayout(mode="single", reason="unknown card types default to a single safe layout")
