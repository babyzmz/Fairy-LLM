from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from app.ui.components.chat.base_card_widget import ChipLabel, HoverCardFrame, extract_domain, open_url
from app.ui.components.chat.chat_message import ChatMessage
from app.ui.components.chat.message_widget import DisplayMode
from app.ui.theme import button_style, mix, resolve_theme


class _NewsCard(HoverCardFrame):
    def __init__(
        self,
        item: dict[str, Any],
        parent=None,
        *,
        language: str = "zh",
        display_mode: DisplayMode = "normal",
    ) -> None:
        theme = resolve_theme(parent)
        super().__init__(parent, theme=theme, radius=16 if display_mode == "normal" else 14, interactive=True, display_mode=display_mode)
        self.item = dict(item)
        self.language = language
        self.setFixedWidth(self.mode_metric(244, 232))
        self.set_palette(
            background=theme.surface,
            hover_background=mix(theme.surface, theme.fairy_blue_soft, 0.06),
            border=mix(theme.divider, theme.fairy_blue_soft, 0.16),
            hover_border=mix(theme.divider, theme.fairy_blue_soft, 0.34),
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(
            self.mode_metric(12, 10),
            self.mode_metric(12, 10),
            self.mode_metric(12, 10),
            self.mode_metric(12, 10),
        )
        root.setSpacing(self.mode_metric(8, 6))

        self.title_label = QLabel(str(item.get("title", "") or ""), self)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(
            f"font-size:{self.mode_metric(13, 12)}px; font-weight:800; color:{theme.text_primary}; border:none; background:transparent;"
        )

        self.summary_label = QLabel(str(item.get("summary", "") or item.get("short_comment", "") or ""), self)
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(
            f"font-size:{self.mode_metric(12, 10)}px; color:{theme.text_secondary}; border:none; background:transparent;"
        )

        tag_wrap = QWidget(self)
        tag_layout = QHBoxLayout(tag_wrap)
        tag_layout.setContentsMargins(0, 0, 0, 0)
        tag_layout.setSpacing(6)
        for tag in list(item.get("tags", []) or [])[: (2 if self.is_compact() else 3)]:
            tag_layout.addWidget(ChipLabel(str(tag), tag_wrap, theme=theme))
        tag_layout.addStretch(1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.domain_label = QLabel(extract_domain(str(item.get("url", "") or "")), self)
        self.domain_label.setStyleSheet(
            f"font-size:{self.mode_metric(11, 10)}px; font-weight:700; color:{theme.text_secondary}; border:none; background:transparent;"
        )
        self.read_button = QPushButton("Read" if language.startswith("en") else "\u9605\u8bfb", self)
        self.read_button.setCursor(Qt.PointingHandCursor)
        self.read_button.setStyleSheet(button_style(theme, tone="accent", radius=12, compact=True))
        self.read_button.clicked.connect(lambda: open_url(str(item.get("url", "") or "")))
        footer.addWidget(self.domain_label, 1)
        footer.addWidget(self.read_button)

        root.addWidget(self.title_label)
        root.addWidget(self.summary_label)
        root.addWidget(tag_wrap)
        root.addLayout(footer)


class NewsCarouselWidget(HoverCardFrame):
    def __init__(
        self,
        message: ChatMessage,
        parent=None,
        *,
        language: str = "zh",
        display_mode: DisplayMode = "normal",
    ) -> None:
        theme = resolve_theme(parent)
        super().__init__(parent, theme=theme, radius=18 if display_mode == "normal" else 16, interactive=False, display_mode=display_mode)
        self.message = message
        self.language = language
        self.setMaximumWidth(self.mode_metric(540, 320))
        self.set_palette(
            background=theme.surface,
            hover_background=theme.surface,
            border=mix(theme.divider, theme.fairy_blue_soft, 0.16),
            hover_border=mix(theme.divider, theme.fairy_blue_soft, 0.16),
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(
            self.mode_metric(14, 12),
            self.mode_metric(14, 12),
            self.mode_metric(14, 12),
            self.mode_metric(14, 12),
        )
        root.setSpacing(self.mode_metric(10, 8))

        self.title_label = QLabel("News" if language.startswith("en") else "\u65b0\u95fb\u901f\u89c8", self)
        self.title_label.setStyleSheet(
            f"font-size:{self.mode_metric(14, 12)}px; font-weight:800; color:{theme.text_primary}; border:none; background:transparent;"
        )

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setStyleSheet("background:transparent; border:none;")

        self.inner = QWidget(self.scroll)
        self.inner_layout = QHBoxLayout(self.inner)
        self.inner_layout.setContentsMargins(0, 0, 0, 0)
        self.inner_layout.setSpacing(self.mode_metric(10, 8))
        self.inner_layout.addStretch(1)
        self.scroll.setWidget(self.inner)

        root.addWidget(self.title_label)
        root.addWidget(self.scroll)
        self.update_message(message)

    def update_message(self, message: ChatMessage) -> None:
        self.message = message
        while self.inner_layout.count():
            item = self.inner_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        items = message.payload.get("items", [])
        if not isinstance(items, list):
            items = []
        limit = 2 if self.is_compact() else 5
        for news_item in items[:limit]:
            if isinstance(news_item, dict):
                self.inner_layout.addWidget(
                    _NewsCard(news_item, self.inner, language=self.language, display_mode=self.display_mode)
                )
        self.inner_layout.addStretch(1)
