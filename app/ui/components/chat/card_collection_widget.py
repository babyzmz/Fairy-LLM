from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.ui.components.chat.base_card_widget import HoverCardFrame
from app.ui.components.chat.card_layout_policy import CardLayoutPolicy, CardLayoutMode
from app.ui.components.chat.card_type_registry import CardTypeRegistry
from app.ui.components.chat.chat_message import ChatMessage
from app.ui.components.chat.message_widget import DisplayMode
from app.ui.theme import mix, resolve_theme


class CardCollectionRenderer(HoverCardFrame):
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
        self._registry = CardTypeRegistry()
        self._layout_policy = CardLayoutPolicy()
        self._last_width = 0
        self._last_height = 0
        self._title = ""
        self._card_type = "news_list"
        self._layout_mode: CardLayoutMode = "masonry"
        self._items: list[dict[str, Any]] = []
        self.setMaximumWidth(self.mode_metric(760, 320))
        self.set_palette(
            background=theme.surface,
            hover_background=theme.surface,
            border=mix(theme.divider, theme.fairy_blue_soft, 0.16),
            hover_border=mix(theme.divider, theme.fairy_blue_soft, 0.16),
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(self.mode_metric(14, 12), self.mode_metric(14, 12), self.mode_metric(14, 12), self.mode_metric(14, 12))
        root.setSpacing(self.mode_metric(10, 8))
        self.root_layout = root

        self.title_label = QLabel(self)
        self.title_label.setStyleSheet(
            f"font-size:{self.mode_metric(14, 12)}px; font-weight:800; color:{theme.text_primary}; border:none; background:transparent;"
        )

        self.columns_host = QWidget(self)
        self.columns_layout = QHBoxLayout(self.columns_host)
        self.columns_layout.setContentsMargins(0, 0, 0, 0)
        self.columns_layout.setSpacing(self.mode_metric(12, 8))

        root.addWidget(self.title_label)
        root.addWidget(self.columns_host)
        self.update_message(message)

    def update_message(self, message: ChatMessage) -> None:
        self.message = message
        payload = message.payload
        items = payload.get("items", [])
        self._title = str(payload.get("title", "") or "").strip()
        self._card_type = str(payload.get("card_type", "") or payload.get("card_schema_type", "") or "news_list").strip().lower()
        self._layout_mode = str(payload.get("layout_mode", "") or payload.get("card_layout", "") or "masonry").strip().lower()  # type: ignore[assignment]
        self._items = [dict(item) for item in items if isinstance(item, dict)]
        self.title_label.setText(self._title)
        fallback_width = min(self.maximumWidth(), self.mode_metric(680, 300))
        self.preferred_height_for_width(self.width() if self.width() > 0 else fallback_width)

    def preferred_height_for_width(self, width: int) -> int:
        self._last_width = max(240, width)
        self._last_height = self._rebuild_columns(self._last_width)
        return self._last_height

    def sizeHint(self) -> QSize:  # noqa: N802
        width = self.width() if self.width() > 0 else (self._last_width or min(self.maximumWidth(), self.mode_metric(680, 300)))
        return QSize(width, self._last_height or self.mode_metric(160, 128))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.preferred_height_for_width(event.size().width())
        self.updateGeometry()

    def _rebuild_columns(self, width: int) -> int:
        while self.columns_layout.count():
            item = self.columns_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        margins = self.root_layout.contentsMargins()
        header_height = self.title_label.sizeHint().height()
        available = max(220, width - margins.left() - margins.right())
        layout = self._layout_policy.resolve(self._layout_mode, available, display_mode=self.display_mode)
        column_count = min(layout.columns, max(1, len(self._items)))
        if not self._items:
            self.columns_host.setMinimumHeight(0)
            self.columns_host.setMaximumHeight(0)
            return margins.top() + header_height + margins.bottom()

        column_spacing = layout.spacing
        column_width = max(160, (available - column_spacing * (column_count - 1)) // max(1, column_count))
        columns: list[tuple[QWidget, QVBoxLayout]] = []
        heights = [0 for _ in range(column_count)]
        for _ in range(column_count):
            column_widget = QWidget(self.columns_host)
            column_layout = QVBoxLayout(column_widget)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setSpacing(column_spacing)
            self.columns_layout.addWidget(column_widget, 1)
            columns.append((column_widget, column_layout))

        for index, item in enumerate(self._items):
            target_index = 0
            if layout.mode == "grid":
                target_index = index % column_count
            elif layout.mode == "masonry":
                target_index = min(range(column_count), key=lambda idx: heights[idx])

            card_widget = self._registry.create_widget(
                self._card_type,
                item,
                columns[target_index][0],
                language=self.language,
                display_mode=self.display_mode,
            )
            if hasattr(card_widget, "setFixedWidth"):
                card_widget.setFixedWidth(column_width)
            if hasattr(card_widget, "preferred_height_for_width"):
                item_height = int(card_widget.preferred_height_for_width(column_width))
            else:
                card_widget.adjustSize()
                item_height = card_widget.sizeHint().height()
            columns[target_index][1].addWidget(card_widget)
            heights[target_index] += item_height + (column_spacing if heights[target_index] else 0)

        max_height = max(heights) if heights else 0
        self.columns_host.setMinimumHeight(max_height)
        self.columns_host.setMaximumHeight(max_height)
        return margins.top() + header_height + self.root_layout.spacing() + max_height + margins.bottom()
