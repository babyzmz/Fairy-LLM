from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLabel, QVBoxLayout

from app.ui.components.chat.base_card_widget import HoverCardFrame
from app.ui.components.chat.chat_message import ChatMessage
from app.ui.components.chat.message_widget import DisplayMode
from app.ui.theme import mix, resolve_theme


class GenericInfoCardWidget(HoverCardFrame):
    def __init__(
        self,
        message: ChatMessage,
        parent=None,
        *,
        language: str = "zh",
        display_mode: DisplayMode = "normal",
    ) -> None:
        theme = resolve_theme(parent)
        super().__init__(parent, theme=theme, radius=18 if display_mode == "normal" else 16, interactive=True, display_mode=display_mode)
        self.message = message
        self.language = language
        self.setMaximumWidth(self.mode_metric(420, 300))
        self.set_palette(
            background=theme.surface,
            hover_background=mix(theme.surface, theme.fairy_blue_soft, 0.06),
            border=mix(theme.divider, theme.fairy_blue_soft, 0.18),
            hover_border=mix(theme.divider, theme.fairy_blue_soft, 0.34),
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(self.mode_metric(14, 12), self.mode_metric(14, 12), self.mode_metric(14, 12), self.mode_metric(14, 12))
        root.setSpacing(self.mode_metric(8, 6))

        self.title_label = QLabel(self)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(
            f"font-size:{self.mode_metric(14, 12)}px; font-weight:800; color:{theme.text_primary}; border:none; background:transparent;"
        )
        self.body_label = QLabel(self)
        self.body_label.setWordWrap(True)
        self.body_label.setStyleSheet(
            f"font-size:{self.mode_metric(12, 11)}px; color:{theme.text_secondary}; border:none; background:transparent;"
        )
        root.addWidget(self.title_label)
        root.addWidget(self.body_label)
        self.update_message(message)

    def update_message(self, message: ChatMessage) -> None:
        self.message = message
        payload = message.payload
        title = str(payload.get("title", "") or payload.get("summary", "") or payload.get("text", "") or "").strip()
        summary = str(payload.get("summary", "") or payload.get("text", "") or "").strip()
        details = payload.get("details")
        if not isinstance(details, list):
            details = []
        fields = payload.get("fields")
        field_lines: list[str] = []
        if isinstance(fields, list):
            for field in fields:
                if not isinstance(field, dict):
                    continue
                label = str(field.get("label", "") or field.get("name", "") or "").strip()
                value = str(field.get("value", "") or "").strip()
                if label and value:
                    field_lines.append(f"{label}: {value}")
        detail_lines = [str(item).strip() for item in details if str(item).strip()]
        body = "\n".join(line for line in ([summary] if summary else []) + field_lines + detail_lines if line)
        self.title_label.setText(title)
        self.body_label.setText(body)
