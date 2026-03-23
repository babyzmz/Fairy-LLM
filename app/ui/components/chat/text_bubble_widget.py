from __future__ import annotations

from datetime import datetime
from math import ceil

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QTextDocument
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout

from app.ui.components.chat.base_card_widget import HoverCardFrame
from app.ui.components.chat.chat_message import ChatMessage
from app.ui.components.chat.message_widget import DisplayMode
from app.ui.theme import mix, resolve_theme


class _AutoSizingRichLabel(QLabel):
    def __init__(self, parent=None, *, color: str = "#FFFFFF") -> None:
        super().__init__(parent)
        self._text_color = color
        self._body_text = ""
        self._rich_text = False
        self._last_height = 0
        self._last_width = 0
        self.setWordWrap(True)
        self.setOpenExternalLinks(True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self._apply_style()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            "QLabel {"
            "background: transparent;"
            "border: none;"
            "padding: 0;"
            f"color: {self._text_color};"
            "}"
        )

    def set_text_color(self, color: str) -> None:
        self._text_color = color
        self._apply_style()

    def set_body(self, text: str, *, rich_text: bool) -> None:
        self._body_text = text or ""
        self._rich_text = bool(rich_text)
        self.setTextFormat(Qt.RichText if self._rich_text else Qt.PlainText)
        self.setText(self._body_text)
        self.sync_height(self.width())

    def _measure_height(self, width: int) -> int:
        usable_width = max(40, width - self.contentsMargins().left() - self.contentsMargins().right())
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(self.font())
        if self._rich_text:
            document.setHtml(self._body_text)
        else:
            document.setPlainText(self._body_text)
        document.setTextWidth(usable_width)
        return ceil(document.size().height()) + 4

    def preferred_height_for_width(self, width: int) -> int:
        return self._measure_height(width)

    def sizeHint(self) -> QSize:  # noqa: N802
        width = self.width() if self.width() > 0 else (self._last_width or 360)
        height = self._last_height or 24
        return QSize(width, height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.sync_height(event.size().width())

    def sync_height(self, width: int) -> None:
        self._last_width = max(40, width)
        height = self.preferred_height_for_width(self._last_width)
        if height == self._last_height:
            return
        self._last_height = height
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.updateGeometry()


class TextBubbleWidget(HoverCardFrame):
    def __init__(
        self,
        message: ChatMessage,
        parent=None,
        *,
        language: str = "zh",
        display_mode: DisplayMode = "normal",
    ) -> None:
        theme = resolve_theme(parent)
        super().__init__(parent, theme=theme, radius=self._radius(display_mode), interactive=True, display_mode=display_mode)
        self.message = message
        self.language = language
        self._last_preferred_width = 0
        self._last_preferred_height = 0
        self.setMaximumWidth(self.mode_metric(520 if message.role != "user" else 480, 300 if message.role != "user" else 280))
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

        self.layout_root = QVBoxLayout(self)
        self.layout_root.setContentsMargins(
            self.mode_metric(16, 12),
            self.mode_metric(14, 10),
            self.mode_metric(16, 12),
            self.mode_metric(14, 10),
        )
        self.layout_root.setSpacing(self.mode_metric(8, 6))

        self.meta_label = QLabel(self)
        meta_font = QFont(self.meta_label.font())
        meta_font.setPointSize(self.mode_metric(11, 10))
        meta_font.setWeight(QFont.DemiBold)
        self.meta_label.setFont(meta_font)

        self.body_view = _AutoSizingRichLabel(self, color=theme.text_primary)
        body_font = QFont(self.body_view.font())
        body_font.setPointSize(self.mode_metric(13, 12))
        body_font.setWeight(QFont.Normal)
        self.body_view.setFont(body_font)

        self.layout_root.addWidget(self.meta_label)
        self.layout_root.addWidget(self.body_view)
        self.update_message(message)

    def _radius(self, display_mode: DisplayMode) -> int:
        return 20 if display_mode == "normal" else 16

    def _display_text(self, text: str) -> str:
        if not self.is_compact():
            return text
        compact_limit = 220
        normalized = " ".join((text or "").split())
        if len(normalized) <= compact_limit:
            return text
        return normalized[: compact_limit - 3].rstrip() + "..."

    def preferred_height_for_width(self, width: int) -> int:
        self._last_preferred_width = width
        margins = self.layout_root.contentsMargins()
        spacing = self.layout_root.spacing()
        meta_height = self.meta_label.sizeHint().height()
        body_width = max(120, width - margins.left() - margins.right())
        body_height = self.body_view.preferred_height_for_width(body_width)
        self.body_view.sync_height(body_width)
        self._last_preferred_height = margins.top() + meta_height + spacing + body_height + margins.bottom()
        return self._last_preferred_height

    def sizeHint(self) -> QSize:  # noqa: N802
        fallback_width = min(self.maximumWidth(), self.mode_metric(420 if self.message.role != "user" else 320, 280))
        width = self.width() if self.width() > 0 else (self._last_preferred_width or fallback_width)
        height = self._last_preferred_height or self.mode_metric(72, 60)
        return QSize(width, height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        margins = self.layout_root.contentsMargins()
        usable_width = max(120, event.size().width() - margins.left() - margins.right())
        self.body_view.sync_height(usable_width)
        self.updateGeometry()

    def update_message(self, message: ChatMessage) -> None:
        self.message = message
        theme = self.theme
        speaker = str(message.payload.get("speaker", "") or "").strip() or {
            "assistant": "Fairy",
            "user": "You" if self.language.startswith("en") else "\u4f60",
            "system": "System" if self.language.startswith("en") else "\u7cfb\u7edf",
        }.get(message.role, "Fairy")
        stamp = datetime.fromtimestamp(message.timestamp).strftime("%H:%M")
        meta_color = theme.text_secondary
        if message.role == "assistant":
            meta_color = theme.fairy_blue_core
            self.set_palette(
                background=mix(theme.surface, theme.fairy_blue_soft, 0.04),
                hover_background=mix(theme.surface, theme.fairy_blue_soft, 0.08),
                border=mix(theme.divider, theme.fairy_blue_soft, 0.26),
                hover_border=mix(theme.divider, theme.fairy_blue_soft, 0.48),
            )
        elif message.role == "user":
            self.set_palette(
                background=theme.surface_soft,
                hover_background=mix(theme.surface_soft, theme.fairy_blue_soft, 0.06),
                border=theme.divider,
                hover_border=mix(theme.divider, theme.fairy_blue_soft, 0.22),
            )
        else:
            self.set_palette(
                background=mix(theme.surface, theme.surface_soft, 0.55),
                hover_background=mix(theme.surface, theme.fairy_blue_soft, 0.05),
                border=mix(theme.divider, theme.fairy_blue_soft, 0.12),
                hover_border=mix(theme.divider, theme.fairy_blue_soft, 0.28),
            )

        self.meta_label.setStyleSheet(f"font-weight:700; color:{meta_color}; border:none; background:transparent;")
        self.body_view.set_text_color(theme.text_primary)
        self.meta_label.setText(f"{speaker} \u00b7 {stamp}")
        body = str(message.payload.get("text", "") or "")
        rich_text = bool(message.payload.get("rich_text", False))
        self.body_view.set_body(self._display_text(body), rich_text=rich_text)
        fallback_width = min(self.maximumWidth(), self.mode_metric(420 if self.message.role != "user" else 320, 280))
        self.preferred_height_for_width(self.width() if self.width() > 0 else fallback_width)
        self.updateGeometry()
