from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtWidgets import QWidget

from app.ui.components.chat.generic_info_card_widget import GenericInfoCardWidget
from app.ui.components.chat.message_widget import DisplayMode, normalize_display_mode
from app.ui.components.chat.news_card_widget import NewsItemCardWidget


CardItemFactory = Callable[[dict[str, Any], QWidget | None, str, DisplayMode], QWidget]

logger = logging.getLogger(__name__)


class CardTypeRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, CardItemFactory] = {
            "news_list": lambda item, parent, language, display_mode: NewsItemCardWidget(
                item,
                parent,
                language=language,
                display_mode=display_mode,
            ),
            "news_card": lambda item, parent, language, display_mode: NewsItemCardWidget(
                item,
                parent,
                language=language,
                display_mode=display_mode,
            ),
            "generic_info": lambda item, parent, language, display_mode: GenericInfoCardWidget(
                _generic_info_message(item),
                parent,
                language=language,
                display_mode=display_mode,
            ),
            "generic_info_card": lambda item, parent, language, display_mode: GenericInfoCardWidget(
                _generic_info_message(item),
                parent,
                language=language,
                display_mode=display_mode,
            ),
        }

    def create_widget(
        self,
        card_type: str,
        payload: dict[str, Any],
        parent: QWidget | None = None,
        *,
        language: str = "zh",
        display_mode: DisplayMode = "normal",
    ) -> QWidget:
        normalized = str(card_type or "generic_info_card").strip().lower()
        factory = self._registry.get(normalized, self._registry["generic_info_card"])
        widget = factory(payload, parent, language, normalize_display_mode(display_mode))
        logger.info(
            "collection_item_renderer_selected item_type=%s renderer=%s fallback=%s",
            normalized,
            widget.__class__.__name__,
            normalized not in self._registry,
        )
        return widget


def _generic_info_message(payload: dict[str, Any]):
    from app.ui.components.chat.chat_message import ChatMessage

    return ChatMessage.create(
        role="assistant",
        message_type="generic_info",
        payload=dict(payload),
    )
