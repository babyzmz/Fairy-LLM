from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QWidget

from app.ui.components.chat.chat_message import ChatMessage
from app.ui.components.chat.image_card_widget import ImageCardWidget
from app.ui.components.chat.link_card_widget import LinkCardWidget
from app.ui.components.chat.map_preview_widget import MapPreviewWidget
from app.ui.components.chat.message_widget import DisplayMode, MessageWidget, normalize_display_mode
from app.ui.components.chat.news_carousel_widget import NewsCarouselWidget
from app.ui.components.chat.suggestion_card_widget import SuggestionCardWidget
from app.ui.components.chat.text_bubble_widget import TextBubbleWidget
from app.ui.components.chat.weather_card_widget import WeatherCardWidget


RendererFactory = Callable[[ChatMessage, QWidget | None, str, DisplayMode], MessageWidget]


class RendererRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, RendererFactory] = {}
        self.register(
            "text",
            lambda message, parent, language, display_mode: TextBubbleWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
        )
        self.register(
            "weather",
            lambda message, parent, language, display_mode: WeatherCardWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
        )
        self.register(
            "map",
            lambda message, parent, language, display_mode: MapPreviewWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
        )
        self.register(
            "image",
            lambda message, parent, language, display_mode: ImageCardWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
        )
        self.register(
            "link",
            lambda message, parent, language, display_mode: LinkCardWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
        )
        self.register(
            "news",
            lambda message, parent, language, display_mode: NewsCarouselWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
        )
        self.register(
            "suggestion",
            lambda message, parent, language, display_mode: SuggestionCardWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
        )

    def register(self, message_type: str, factory: RendererFactory) -> None:
        self._registry[(message_type or "text").strip().lower()] = factory

    def create_widget(
        self,
        message: ChatMessage,
        parent: QWidget | None = None,
        *,
        language: str = "zh",
        display_mode: DisplayMode = "normal",
    ) -> MessageWidget:
        factory = self._registry.get((message.type or "text").strip().lower(), self._registry["text"])
        return factory(message, parent, language, normalize_display_mode(display_mode))
