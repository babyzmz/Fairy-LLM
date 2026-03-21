from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.ui.components.chat.base_card_widget import HoverCardFrame, extract_domain, open_url
from app.ui.components.chat.chat_message import ChatMessage
from app.ui.components.chat.message_widget import DisplayMode
from app.ui.theme import button_style, mix, resolve_theme


class LinkCardWidget(HoverCardFrame):
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
        self._target_url = ""
        self.setMaximumWidth(self.mode_metric(420, 300))
        self.set_palette(
            background=theme.surface,
            hover_background=mix(theme.surface, theme.fairy_blue_soft, 0.05),
            border=mix(theme.divider, theme.fairy_blue_soft, 0.18),
            hover_border=mix(theme.divider, theme.fairy_blue_soft, 0.34),
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(
            self.mode_metric(14, 12),
            self.mode_metric(14, 12),
            self.mode_metric(14, 12),
            self.mode_metric(14, 12),
        )
        root.setSpacing(self.mode_metric(12, 10))

        self.icon_label = QLabel("\u2197", self)
        self.icon_label.setAlignment(Qt.AlignCenter)
        icon_size = self.mode_metric(36, 30)
        self.icon_label.setFixedSize(icon_size, icon_size)
        self.icon_label.setStyleSheet(
            f"background:{mix(theme.fairy_blue_core, theme.fairy_blue_soft, 0.42)};"
            "border:none;"
            f"border-radius:{icon_size // 2}px;"
            f"font-size:{self.mode_metric(16, 14)}px;"
            "font-weight:800;"
            "color:#F8FBFF;"
        )

        text_wrap = QVBoxLayout()
        text_wrap.setSpacing(3)
        self.title_label = QLabel(self)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(
            f"font-size:{self.mode_metric(14, 12)}px; font-weight:800; color:{theme.text_primary}; border:none; background:transparent;"
        )
        self.domain_label = QLabel(self)
        self.domain_label.setStyleSheet(
            f"font-size:{self.mode_metric(11, 10)}px; font-weight:700; color:{theme.text_secondary}; border:none; background:transparent;"
        )
        text_wrap.addWidget(self.title_label)
        text_wrap.addWidget(self.domain_label)

        self.open_button = QPushButton("Open" if language.startswith("en") else "\u6253\u5f00", self)
        self.open_button.setCursor(Qt.PointingHandCursor)
        self.open_button.setStyleSheet(button_style(theme, tone="neutral", radius=12, compact=True))
        self.open_button.clicked.connect(self._open_target)

        root.addWidget(self.icon_label)
        root.addLayout(text_wrap, 1)
        root.addWidget(self.open_button)
        self.update_message(message)

    def update_message(self, message: ChatMessage) -> None:
        self.message = message
        payload = message.payload
        self._target_url = str(payload.get("url", "") or "").strip()
        title = str(payload.get("title", "") or payload.get("label", "") or self._target_url).strip()
        self.title_label.setText(title)
        self.domain_label.setText(extract_domain(self._target_url))

    def _open_target(self) -> None:
        open_url(self._target_url)
