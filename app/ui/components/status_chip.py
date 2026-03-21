from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from app.ui.theme import resolve_theme, tone_colors


class StatusChip(QLabel):
    def __init__(self, text: str = "", tone: str = "neutral", parent=None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setTextFormat(Qt.PlainText)
        self.setMinimumHeight(24)
        self.set_chip(text, tone)

    def set_chip(self, text: str, tone: str = "neutral") -> None:
        theme = resolve_theme(self)
        fg, bg, border = tone_colors(theme, tone)
        self.setText(text)
        self.setStyleSheet(
            f"""
            QLabel {{
                color: {fg};
                background: {bg};
                border: 1px solid {border};
                border-radius: 12px;
                padding: 3px 10px 4px 10px;
                font-size: 11px;
                font-weight: 700;
            }}
            """
        )
