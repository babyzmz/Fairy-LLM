from __future__ import annotations

from typing import Literal

from PySide6.QtWidgets import QFrame, QSizePolicy

from app.ui.components.chat.chat_message import ChatMessage


DisplayMode = Literal["normal", "compact"]


def normalize_display_mode(display_mode: str) -> DisplayMode:
    return "compact" if str(display_mode).strip().lower() == "compact" else "normal"


class MessageWidget(QFrame):
    def __init__(
        self,
        message: ChatMessage | None = None,
        parent=None,
        *,
        display_mode: DisplayMode = "normal",
    ) -> None:
        super().__init__(parent)
        self.message = message
        self.display_mode: DisplayMode = normalize_display_mode(display_mode)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

    def is_compact(self) -> bool:
        return self.display_mode == "compact"

    def mode_metric(self, normal: int, compact: int) -> int:
        return compact if self.is_compact() else normal

    def update_message(self, message: ChatMessage) -> None:
        self.message = message

    def content_max_width(self) -> int:
        limit = self.maximumWidth()
        return limit if limit > 0 else self.mode_metric(560, 320)

    def preferred_height_for_width(self, width: int) -> int:
        self.setFixedWidth(width)
        self.updateGeometry()
        self.adjustSize()
        return max(self.minimumSizeHint().height(), self.sizeHint().height())

