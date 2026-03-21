from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.ui.components.chat.base_card_widget import ClickableAsyncImageLabel, HoverCardFrame, open_image_preview, open_url
from app.ui.components.chat.chat_message import ChatMessage
from app.ui.components.chat.message_widget import DisplayMode
from app.ui.theme import button_style, mix, resolve_theme


class ImageCardWidget(HoverCardFrame):
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
        self._image_source = ""
        self._target_url = ""
        self.setMaximumWidth(self.mode_metric(360, 300))
        self.set_palette(
            background=theme.surface,
            hover_background=mix(theme.surface, theme.fairy_blue_soft, 0.06),
            border=mix(theme.divider, theme.fairy_blue_soft, 0.18),
            hover_border=mix(theme.divider, theme.fairy_blue_soft, 0.34),
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(
            self.mode_metric(14, 12),
            self.mode_metric(14, 12),
            self.mode_metric(14, 12),
            self.mode_metric(14, 12),
        )
        root.setSpacing(self.mode_metric(10, 8))

        self.preview_label = ClickableAsyncImageLabel(
            self,
            theme=theme,
            placeholder="Image Preview" if language.startswith("en") else "\u56fe\u7247\u9884\u89c8",
            minimum_height=self.mode_metric(168, 120),
            corner_radius=16 if display_mode == "normal" else 14,
        )
        self.preview_label.clicked.connect(self._open_preview)

        self.title_label = QLabel(self)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(
            f"font-size:{self.mode_metric(14, 12)}px; font-weight:800; color:{theme.text_primary}; border:none; background:transparent;"
        )

        self.caption_label = QLabel(self)
        self.caption_label.setWordWrap(True)
        self.caption_label.setStyleSheet(
            f"font-size:{self.mode_metric(12, 10)}px; color:{theme.text_secondary}; border:none; background:transparent;"
        )

        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addStretch(1)

        self.preview_button = QPushButton("Preview" if language.startswith("en") else "\u653e\u5927\u67e5\u770b", self)
        self.preview_button.setCursor(Qt.PointingHandCursor)
        self.preview_button.setStyleSheet(button_style(theme, tone="neutral", radius=12, compact=True))
        self.preview_button.clicked.connect(self._open_preview)

        self.open_button = QPushButton("Open" if language.startswith("en") else "\u6253\u5f00", self)
        self.open_button.setCursor(Qt.PointingHandCursor)
        self.open_button.setStyleSheet(button_style(theme, tone="accent", radius=12, compact=True))
        self.open_button.clicked.connect(self._open_target)

        footer.addWidget(self.preview_button)
        footer.addWidget(self.open_button)

        root.addWidget(self.preview_label)
        root.addWidget(self.title_label)
        root.addWidget(self.caption_label)
        root.addLayout(footer)
        self.update_message(message)

    def update_message(self, message: ChatMessage) -> None:
        self.message = message
        payload = message.payload
        self._image_source = str(payload.get("image", "") or payload.get("image_path", "") or payload.get("image_url", "") or "").strip()
        self._target_url = str(payload.get("url", "") or "").strip()
        title = str(payload.get("title", "") or ("Image" if self.language.startswith("en") else "\u56fe\u7247")).strip()
        caption = str(payload.get("caption", "") or payload.get("summary", "") or "").strip()
        if self.is_compact() and len(caption) > 90:
            caption = caption[:87].rstrip() + "..."
        self.title_label.setText(title)
        self.caption_label.setVisible(bool(caption))
        self.caption_label.setText(caption)
        self.preview_label.set_source(self._image_source)
        self.preview_button.setVisible(bool(self._image_source))
        self.open_button.setVisible(bool(self._target_url))

    def _open_preview(self) -> None:
        open_image_preview(self._image_source, title=self.title_label.text(), theme=self.theme, parent=self)

    def _open_target(self) -> None:
        open_url(self._target_url)
