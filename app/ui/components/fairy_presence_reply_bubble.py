from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from app.ui.components.chat import ChatMessage, MessageWidget, RendererRegistry
from app.ui.i18n import LANG_ZH, normalize_ui_language, tr
from app.ui.theme import apply_soft_shadow, resolve_theme, rgba

logger = logging.getLogger(__name__)


class FairyPresenceReplyBubble(QFrame):
    closeRequested = Signal()
    hoverChanged = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        theme = resolve_theme(self)
        self._language = LANG_ZH
        self._renderer_registry = RendererRegistry()
        self._message_widget: MessageWidget | None = None
        self._message: ChatMessage | None = None
        self._content_width = 300

        self.setObjectName("presenceReplyBubble")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self.setMaximumWidth(332)
        self.setStyleSheet(
            f"""
            QFrame#presenceReplyBubble {{
                background: {rgba(theme.surface, 236)};
                border: 1px solid {rgba(theme.divider, 220)};
                border-radius: 20px;
            }}
            QPushButton {{
                background: transparent;
                border: none;
                color: {theme.text_secondary};
                min-width: 20px;
                min-height: 20px;
                padding: 0px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                color: {theme.text_primary};
            }}
            QWidget#presenceReplyContent {{
                background: transparent;
                border: none;
            }}
            """
        )
        apply_soft_shadow(self, theme, blur=22, y_offset=8, strength=0.8)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.addStretch(1)

        self.close_button = QPushButton("x", self)
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.clicked.connect(self.closeRequested.emit)
        header.addWidget(self.close_button)

        self.content_host = QWidget(self)
        self.content_host.setObjectName("presenceReplyContent")
        self.content_host.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        self.content_layout = QVBoxLayout(self.content_host)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)

        root.addLayout(header)
        root.addWidget(self.content_host, 0, Qt.AlignRight)

        self.set_ui_language(self._language)
        self.hide()

    def set_ui_language(self, language: str) -> None:
        self._language = normalize_ui_language(language)
        self.close_button.setToolTip(tr("presence_close_reply", self._language))
        if self._message is not None:
            self.set_message(self._message)

    def set_message(self, message: ChatMessage) -> None:
        self._message = message
        self._clear_content_widget()
        self._message_widget = self._renderer_registry.create_widget(
            message,
            self.content_host,
            language=self._language,
            display_mode="compact",
        )
        self.content_layout.addWidget(self._message_widget, 0, Qt.AlignRight | Qt.AlignTop)
        self._sync_widget_geometry()
        self.show()

    def clear_reply(self) -> None:
        self._message = None
        self._clear_content_widget()
        self.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_widget_geometry()

    def enterEvent(self, event) -> None:  # noqa: N802
        self.hoverChanged.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hoverChanged.emit(False)
        super().leaveEvent(event)

    def _sync_widget_geometry(self) -> None:
        """Synchronize message widget geometry with defensive bounds checking.

        Prevents invalid geometry that could cause UpdateLayeredWindowIndirect errors
        on Windows when rendering card widgets.
        """
        if self._message_widget is None:
            return
        try:
            available_width = max(220, min(self._content_width, self.width() - 16))
            widget_width = min(available_width, self._message_widget.content_max_width())
            widget_height = self._message_widget.preferred_height_for_width(widget_width)

            # Ensure valid dimensions
            if widget_width <= 0 or widget_height <= 0:
                logger.warning(
                    "invalid_widget_geometry width=%d height=%d, skipping sync",
                    widget_width,
                    widget_height,
                )
                return

            self._message_widget.setFixedWidth(widget_width)
            self._message_widget.setMinimumHeight(widget_height)
            self._message_widget.setMaximumHeight(widget_height)
            self.content_host.adjustSize()
            self.adjustSize()
            self.updateGeometry()

            # Log geometry for debugging
            logger.debug(
                "reply_bubble_geometry_synced widget_size=(%d,%d) bubble_size=(%d,%d)",
                widget_width,
                widget_height,
                self.width(),
                self.height(),
            )
        except Exception as e:
            logger.exception("sync_widget_geometry failed: %s", e)

    def _clear_content_widget(self) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._message_widget = None
