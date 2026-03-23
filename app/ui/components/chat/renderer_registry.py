from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtWidgets import QWidget

from app.ui.components.chat.card_renderer_registry import CardRendererRegistry
from app.ui.components.chat.chat_message import ChatMessage
from app.ui.components.chat.message_widget import DisplayMode, MessageWidget, normalize_display_mode
from app.ui.components.chat.text_bubble_widget import TextBubbleWidget


logger = logging.getLogger(__name__)

RendererFactory = Callable[[ChatMessage, QWidget | None, str, DisplayMode], MessageWidget]


class RendererRegistry:
    def __init__(self) -> None:
        self._text_registry: dict[str, RendererFactory] = {}
        self._card_registry = CardRendererRegistry()
        self.register(
            "text",
            lambda message, parent, language, display_mode: TextBubbleWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
        )

    def register(self, message_type: str, factory: RendererFactory) -> None:
        self._text_registry[(message_type or "text").strip().lower()] = factory

    def create_widget(
        self,
        message: ChatMessage,
        parent: QWidget | None = None,
        *,
        language: str = "zh",
        display_mode: DisplayMode = "normal",
    ) -> MessageWidget:
        normalized_mode = normalize_display_mode(display_mode)
        normalized_type = (message.type or "text").strip().lower()
        if normalized_type == "text":
            factory = self._text_registry["text"]
            widget = factory(message, parent, language, normalized_mode)
            logger.info("message_renderer_selected message_type=text renderer=%s", widget.__class__.__name__)
            return widget
        return self._card_registry.create_widget(
            message,
            parent,
            language=language,
            display_mode=normalized_mode,
        )
