from __future__ import annotations

from datetime import datetime
from math import ceil

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QTextDocument
from PySide6.QtWidgets import QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

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


# ---------------------------------------------------------------------------
# Max scrollable body height: 60% of screen height, clamped 320–640 px
# ---------------------------------------------------------------------------
def _max_body_height() -> int:
    try:
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            return max(320, min(640, round(screen.availableGeometry().height() * 0.60)))
    except Exception:
        pass
    return 480


# ---------------------------------------------------------------------------
# Scrollable body container with top/bottom fade gradient overlay
# ---------------------------------------------------------------------------
class _ScrollableBody(QWidget):
    """Wraps _AutoSizingRichLabel in a QScrollArea.

    - Caps display height at max_height; content is scrollable inside.
    - Paints a gradient fade at top/bottom to hint scrollability.
    - Scrollbar is thin and unobtrusive (4 px).
    """

    _FADE_H = 28  # gradient zone height in px

    def __init__(
        self,
        label: "_AutoSizingRichLabel",
        parent=None,
        *,
        max_height: int = 480,
        bg_color: str = "transparent",
    ) -> None:
        super().__init__(parent)
        self._max_height = max_height
        self._bg_color = bg_color
        self._content_height = 0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:vertical {"
            "  width: 4px; background: transparent; margin: 0;"
            "}"
            "QScrollBar::handle:vertical {"
            "  background: rgba(140,160,200,0.38);"
            "  border-radius: 2px; min-height: 20px;"
            "}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        # inner widget: adds 6 px right padding so text never hides behind scrollbar
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 6, 0)
        inner_layout.setSpacing(0)
        inner_layout.addWidget(label)
        inner_layout.addStretch(0)
        self._scroll.setWidget(inner)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._scroll)

        # repaint on scroll to update fade overlay
        self._scroll.verticalScrollBar().valueChanged.connect(lambda _: self.update())

    def set_bg_color(self, color: str) -> None:
        self._bg_color = color
        self.update()

    def set_content_height(self, height: int) -> None:
        """Called when label content height is known."""
        self._content_height = height
        display_h = min(height, self._max_height)
        self._scroll.setFixedHeight(display_h)
        self.setFixedHeight(display_h)
        self.update()

    def _is_scrollable(self) -> bool:
        return self._content_height > self._max_height

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self._is_scrollable():
            return
        sb = self._scroll.verticalScrollBar()
        if sb is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        fade = min(self._FADE_H, h // 4)
        try:
            bg = QColor(self._bg_color)
            if not bg.isValid():
                bg = QColor(0, 0, 0, 0)
        except Exception:
            bg = QColor(0, 0, 0, 0)
        transp = QColor(bg)
        transp.setAlpha(0)
        if sb.value() > 0:
            g = QLinearGradient(0, 0, 0, fade)
            g.setColorAt(0.0, bg)
            g.setColorAt(1.0, transp)
            painter.fillRect(0, 0, w, fade, g)
        if sb.value() < sb.maximum():
            g = QLinearGradient(0, h - fade, 0, h)
            g.setColorAt(0.0, transp)
            g.setColorAt(1.0, bg)
            painter.fillRect(0, h - fade, w, fade, g)
        painter.end()


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
        self._is_assistant = message.role == "assistant"
        self._max_body_h = _max_body_height() if self._is_assistant else 10_000
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

        # Header — always visible, never scrolls
        self.meta_label = QLabel(self)
        meta_font = QFont(self.meta_label.font())
        meta_font.setPointSize(self.mode_metric(11, 10))
        meta_font.setWeight(QFont.DemiBold)
        self.meta_label.setFont(meta_font)

        # Body label
        self.body_view = _AutoSizingRichLabel(self, color=theme.text_primary)
        body_font = QFont(self.body_view.font())
        body_font.setPointSize(self.mode_metric(13, 12))
        body_font.setWeight(QFont.Normal)
        self.body_view.setFont(body_font)
        self.body_view.setStyleSheet(
            f"color:{theme.text_primary}; border:none; background:transparent;"
        )

        # Scrollable container for assistant messages
        if self._is_assistant:
            self._scroll_body: _ScrollableBody | None = _ScrollableBody(
                self.body_view,
                self,
                max_height=self._max_body_h,
                bg_color=mix(theme.surface, theme.fairy_blue_soft, 0.04),
            )
            self.layout_root.addWidget(self.meta_label)
            self.layout_root.addWidget(self._scroll_body)
        else:
            self._scroll_body = None
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
        full_body_h = self.body_view.preferred_height_for_width(body_width)

        if self._scroll_body is not None:
            self.body_view.sync_height(body_width)
            self._scroll_body.set_content_height(full_body_h)
            displayed_body_h = min(full_body_h, self._max_body_h)
        else:
            displayed_body_h = full_body_h

        self._last_preferred_height = (
            margins.top() + meta_height + spacing + displayed_body_h + margins.bottom()
        )
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
        if self._scroll_body is not None:
            full_body_h = self.body_view.preferred_height_for_width(usable_width)
            self._scroll_body.set_content_height(full_body_h)
        self.updateGeometry()

    def update_message(self, message: ChatMessage) -> None:
        self.message = message
        self._is_assistant = message.role == "assistant"
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
            bg = mix(theme.surface, theme.fairy_blue_soft, 0.04)
            self.set_palette(
                background=bg,
                hover_background=mix(theme.surface, theme.fairy_blue_soft, 0.08),
                border=mix(theme.divider, theme.fairy_blue_soft, 0.26),
                hover_border=mix(theme.divider, theme.fairy_blue_soft, 0.48),
            )
            if self._scroll_body is not None:
                self._scroll_body.set_bg_color(bg)
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

        self.meta_label.setStyleSheet(
            f"font-weight:700; color:{meta_color}; border:none; background:transparent;"
        )
        self.body_view.set_text_color(theme.text_primary)
        self.meta_label.setText(f"{speaker} \u00b7 {stamp}")
        body = str(message.payload.get("text", "") or "")
        rich_text = bool(message.payload.get("rich_text", False))
        self.body_view.set_body(self._display_text(body), rich_text=rich_text)
        fallback_width = min(self.maximumWidth(), self.mode_metric(420 if self.message.role != "user" else 320, 280))
        self.preferred_height_for_width(
            self.width() if self.width() > 0 else fallback_width
        )
        self.updateGeometry()
