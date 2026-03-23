from __future__ import annotations

from app.ui.components.chat.card_collection_widget import CardCollectionRenderer
from app.ui.components.chat.chat_message import ChatMessage
from app.ui.components.chat.message_widget import DisplayMode


class NewsCarouselWidget(CardCollectionRenderer):
    """Compatibility wrapper for the shared card collection renderer.

    The old horizontal carousel has been retired. News cards now render
    through the shared vertical grid/masonry pipeline.
    """

    def __init__(
        self,
        message: ChatMessage,
        parent=None,
        *,
        language: str = "zh",
        display_mode: DisplayMode = "normal",
    ) -> None:
        super().__init__(self._normalize_message(message, language, display_mode), parent, language=language, display_mode=display_mode)

    def update_message(self, message: ChatMessage) -> None:
        super().update_message(self._normalize_message(message, self.language, self.display_mode))

    def _normalize_message(self, message: ChatMessage, language: str, display_mode: DisplayMode) -> ChatMessage:
        payload = dict(message.payload)
        items = payload.get("items", [])
        if isinstance(items, list) and display_mode == "compact":
            payload["items"] = items[:2]
        payload.setdefault("title", "News Briefing" if language.startswith("en") else "\u65b0\u95fb\u901f\u89c8")
        payload.setdefault("card_type", "news_list")
        payload.setdefault("card_schema_type", "news_list")
        payload.setdefault("layout_mode", "masonry")
        return ChatMessage.create(
            role=message.role,
            message_type=message.type,
            payload=payload,
            message_id=message.id,
            timestamp=message.timestamp,
        )
