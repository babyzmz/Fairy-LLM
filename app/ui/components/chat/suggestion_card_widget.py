from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from app.ui.components.chat.base_card_widget import HoverCardFrame
from app.ui.components.chat.chat_message import ChatMessage
from app.ui.components.chat.message_widget import DisplayMode
from app.ui.theme import mix, resolve_theme


class SuggestionCardWidget(HoverCardFrame):
    def __init__(
        self,
        message: ChatMessage,
        parent=None,
        *,
        language: str = "zh",
        display_mode: DisplayMode = "normal",
    ) -> None:
        theme = resolve_theme(parent)
        super().__init__(parent, theme=theme, radius=18 if display_mode == "normal" else 16, interactive=True, display_mode=display_mode)
        self.message = message
        self.language = language
        self.setMaximumWidth(self.mode_metric(440, 300))
        self.set_palette(
            background=mix(theme.surface, theme.fairy_blue_soft, 0.05),
            hover_background=mix(theme.surface, theme.fairy_blue_soft, 0.1),
            border=mix(theme.divider, theme.fairy_blue_soft, 0.22),
            hover_border=mix(theme.divider, theme.fairy_blue_soft, 0.36),
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(
            self.mode_metric(16, 12),
            self.mode_metric(16, 12),
            self.mode_metric(16, 12),
            self.mode_metric(16, 12),
        )
        root.setSpacing(self.mode_metric(10, 8))

        header = QHBoxLayout()
        header.setSpacing(10)
        icon_size = self.mode_metric(30, 26)
        self.icon_label = QLabel("F", self)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setFixedSize(icon_size, icon_size)
        self.icon_label.setStyleSheet(
            f"background:{mix(theme.fairy_blue_core, theme.fairy_blue_soft, 0.46)};"
            "border:none;"
            f"border-radius:{icon_size // 2}px;"
            f"font-size:{self.mode_metric(14, 12)}px;"
            "font-weight:900;"
            "color:#F8FBFF;"
        )
        self.title_label = QLabel("Fairy Suggestion" if language.startswith("en") else "Fairy \u5efa\u8bae", self)
        self.title_label.setStyleSheet(
            f"font-size:{self.mode_metric(14, 12)}px; font-weight:800; color:{theme.text_primary}; border:none; background:transparent;"
        )
        header.addWidget(self.icon_label)
        header.addWidget(self.title_label)
        header.addStretch(1)

        self.body_label = QLabel(self)
        self.body_label.setWordWrap(True)
        self.body_label.setOpenExternalLinks(True)
        self.body_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.body_label.setStyleSheet(
            f"font-size:{self.mode_metric(13, 12)}px; line-height:1.65; color:{theme.text_primary}; border:none; background:transparent;"
        )

        root.addLayout(header)
        root.addWidget(self.body_label)
        self.update_message(message)

    def update_message(self, message: ChatMessage) -> None:
        self.message = message
        body = str(message.payload.get("body", "") or message.payload.get("text", "") or "").strip()
        if self.is_compact() and len(body) > 180:
            body = body[:177].rstrip() + "..."
        if not bool(message.payload.get("rich_text", False)):
            body = html.escape(body).replace("\n", "<br>")
        self.body_label.setText(body)
