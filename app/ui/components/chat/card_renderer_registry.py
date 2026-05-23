from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtWidgets import QWidget

from app.ui.components.chat.chat_message import ChatMessage
from app.ui.components.chat.generic_info_card_widget import GenericInfoCardWidget
from app.ui.components.chat.image_card_widget import ImageCardWidget
from app.ui.components.chat.link_card_widget import LinkCardWidget
from app.ui.components.chat.map_preview_widget import MapPreviewWidget
from app.ui.components.chat.message_widget import DisplayMode, MessageWidget, normalize_display_mode
from app.ui.components.chat.news_carousel_widget import NewsCarouselWidget
from app.ui.components.chat.suggestion_card_widget import SuggestionCardWidget
from app.ui.components.chat.weather_card_widget import WeatherCardWidget
from app.ui.components.chat.web_result_card_widgets import (
    CompareCardWidget,
    ReleaseCardWidget,
    SpecsCardWidget,
    WebBriefCardWidget,
)


logger = logging.getLogger(__name__)

CardRendererFactory = Callable[[ChatMessage, QWidget | None, str, DisplayMode], MessageWidget]


class CardRendererRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, CardRendererFactory] = {
            "weather": lambda message, parent, language, display_mode: WeatherCardWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
            "location": lambda message, parent, language, display_mode: MapPreviewWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
            "news_list": lambda message, parent, language, display_mode: NewsCarouselWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
            "generic_info": lambda message, parent, language, display_mode: GenericInfoCardWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
            "specs": lambda message, parent, language, display_mode: SpecsCardWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
            "compare": lambda message, parent, language, display_mode: CompareCardWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
            "release": lambda message, parent, language, display_mode: ReleaseCardWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
            "web_brief": lambda message, parent, language, display_mode: WebBriefCardWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
            "image": lambda message, parent, language, display_mode: ImageCardWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
            "link": lambda message, parent, language, display_mode: LinkCardWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
            "suggestion": lambda message, parent, language, display_mode: SuggestionCardWidget(
                message,
                parent,
                language=language,
                display_mode=display_mode,
            ),
        }
        self._message_type_map = {
            "weather": "weather",
            "map": "location",
            "news": "news_list",
            "generic_info": "generic_info",
            "specs": "specs",
            "compare": "compare",
            "release": "release",
            "web_brief": "web_brief",
            "image": "image",
            "link": "link",
            "suggestion": "suggestion",
        }
        self._compat_type_map = {
            "weather_card": "weather",
            "location_map_card": "location",
            "map_card": "location",
            "news_card": "news_list",
            "generic_info_card": "generic_info",
        }

    def register(self, card_type: str, factory: CardRendererFactory) -> None:
        self._registry[self._canonicalize(card_type)] = factory

    def create_widget(
        self,
        message: ChatMessage,
        parent: QWidget | None = None,
        *,
        language: str = "zh",
        display_mode: DisplayMode = "normal",
    ) -> MessageWidget:
        card_type = self.resolve_card_type(message)
        payload = message.payload if isinstance(message.payload, dict) else {}
        metadata = payload.get("card_metadata") if isinstance(payload.get("card_metadata"), dict) else {}
        factory = self._registry.get(card_type, self._registry["generic_info"])
        widget = factory(message, parent, language, normalize_display_mode(display_mode))
        logger.info(
            "card_renderer_selected card_type=%s renderer=%s fallback=%s fallback_reason=%s schema_issues=%s",
            card_type,
            widget.__class__.__name__,
            card_type not in self._registry,
            str(metadata.get("fallback_reason", "") or ""),
            ",".join(str(item) for item in list(metadata.get("schema_issues", []) or [])),
        )
        return widget

    def resolve_card_type(self, message: ChatMessage) -> str:
        payload = message.payload if isinstance(message.payload, dict) else {}
        explicit_type = (
            str(payload.get("card_type") or payload.get("card_schema_type") or payload.get("schema_type") or "").strip().lower()
        )
        if explicit_type:
            return self._canonicalize(explicit_type)
        return self._message_type_map.get(message.type, "generic_info")

    def _canonicalize(self, card_type: str) -> str:
        normalized = str(card_type or "").strip().lower()
        return self._compat_type_map.get(normalized, normalized or "generic_info")
