from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.ui.components.chat.message_widget import DisplayMode, normalize_display_mode


CardLayoutMode = Literal["single", "grid", "masonry"]


@dataclass(slots=True)
class ResolvedCardLayout:
    mode: CardLayoutMode
    columns: int
    spacing: int


class CardLayoutPolicy:
    def resolve(self, layout_mode: str, width: int, *, display_mode: DisplayMode = "normal") -> ResolvedCardLayout:
        normalized_mode: CardLayoutMode = self._normalize_mode(layout_mode)
        display_mode = normalize_display_mode(display_mode)
        spacing = 12 if display_mode == "normal" else 8

        if normalized_mode == "single":
            return ResolvedCardLayout(mode="single", columns=1, spacing=spacing)

        if width < 460:
            columns = 1
        elif width < 820:
            columns = 2
        elif width < 1120:
            columns = 3
        else:
            columns = 4

        if display_mode == "compact":
            columns = min(columns, 2)

        return ResolvedCardLayout(mode=normalized_mode, columns=max(1, columns), spacing=spacing)

    def _normalize_mode(self, layout_mode: str) -> CardLayoutMode:
        normalized = str(layout_mode or "single").strip().lower()
        if normalized in {"grid", "masonry"}:
            return normalized
        return "single"
