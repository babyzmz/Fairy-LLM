from __future__ import annotations

import html
from urllib.parse import urlparse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout

from app.ui.components.chat.base_card_widget import HoverCardFrame
from app.ui.components.chat.chat_message import ChatMessage
from app.ui.components.chat.message_widget import DisplayMode
from app.ui.theme import mix, resolve_theme


def _host_from_url(url: str) -> str:
    cleaned = str(url or "").strip()
    if not cleaned:
        return ""
    return urlparse(cleaned).netloc or cleaned


class _WebCardBase(HoverCardFrame):
    def __init__(
        self,
        message: ChatMessage,
        parent=None,
        *,
        language: str = "zh",
        display_mode: DisplayMode = "normal",
        background: str = "",
        hover_background: str = "",
        border: str = "",
        hover_border: str = "",
    ) -> None:
        theme = resolve_theme(parent)
        super().__init__(parent, theme=theme, radius=18 if display_mode == "normal" else 16, interactive=True, display_mode=display_mode)
        self.message = message
        self.language = language
        self.setMaximumWidth(self.mode_metric(430, 320))
        self._footer_link_color = mix(theme.text_primary, theme.fairy_blue_soft, 0.45)
        self.set_palette(
            background=background or theme.surface,
            hover_background=hover_background or mix(theme.surface, theme.fairy_blue_soft, 0.08),
            border=border or mix(theme.divider, theme.fairy_blue_soft, 0.18),
            hover_border=hover_border or mix(theme.divider, theme.fairy_blue_soft, 0.34),
        )

        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(self.mode_metric(14, 12), self.mode_metric(14, 12), self.mode_metric(14, 12), self.mode_metric(14, 12))
        self.root.setSpacing(self.mode_metric(8, 6))

        self.title_label = QLabel(self)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(
            f"font-size:{self.mode_metric(15, 13)}px; font-weight:800; color:{theme.text_primary}; border:none; background:transparent;"
        )
        self.summary_label = QLabel(self)
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(
            f"font-size:{self.mode_metric(12, 11)}px; color:{theme.text_secondary}; border:none; background:transparent;"
        )
        self.footer_label = QLabel(self)
        self.footer_label.setWordWrap(True)
        self.footer_label.setOpenExternalLinks(True)
        self.footer_label.setTextFormat(Qt.RichText)
        self.footer_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.footer_label.setStyleSheet(
            f"font-size:{self.mode_metric(11, 10)}px; color:{mix(theme.text_secondary, theme.text_primary, 0.34)}; border:none; background:transparent;"
        )

        self.root.addWidget(self.title_label)
        self.root.addWidget(self.summary_label)

    def _finalize(self) -> None:
        self.root.addWidget(self.footer_label)

    def _set_common(self, payload: dict[str, object]) -> None:
        self.title_label.setText(str(payload.get("title", "") or "").strip())
        self.summary_label.setText(str(payload.get("summary", "") or "").strip())

    def _set_footer(self, source_url: str, source_label: str = "") -> None:
        host = str(source_label or "").strip() or _host_from_url(source_url)
        if not host:
            self.footer_label.clear()
            self.footer_label.setToolTip("")
            self.footer_label.setVisible(False)
            return
        label = f"来源: {host}"
        self.footer_label.setToolTip(source_url)
        if source_url:
            self.footer_label.setText(
                f'<a href="{html.escape(source_url, quote=True)}" '
                f'style="color:{self._footer_link_color}; text-decoration:none;">{html.escape(label)}</a>'
            )
        else:
            self.footer_label.setText(html.escape(label))
        self.footer_label.setVisible(True)


class SpecsCardWidget(_WebCardBase):
    def __init__(self, message: ChatMessage, parent=None, *, language: str = "zh", display_mode: DisplayMode = "normal") -> None:
        theme = resolve_theme(parent)
        super().__init__(
            message,
            parent,
            language=language,
            display_mode=display_mode,
            background=mix(theme.surface, "#EAF4FF", 0.72),
            hover_background=mix(theme.surface, "#D7EAFF", 0.86),
            border=mix(theme.divider, "#7AA9D8", 0.28),
            hover_border=mix(theme.divider, "#4A88C7", 0.38),
        )
        self.field_grid = QGridLayout()
        self.field_grid.setHorizontalSpacing(self.mode_metric(10, 8))
        self.field_grid.setVerticalSpacing(self.mode_metric(6, 5))
        self.field_name_labels: list[QLabel] = []
        self.field_value_labels: list[QLabel] = []
        for row in range(6):
            name_label = QLabel(self)
            name_label.setWordWrap(True)
            name_label.setStyleSheet(
                f"font-size:{self.mode_metric(11, 10)}px; font-weight:700; color:{theme.text_primary}; border:none; background:transparent;"
            )
            value_label = QLabel(self)
            value_label.setWordWrap(True)
            value_label.setStyleSheet(
                f"font-size:{self.mode_metric(11, 10)}px; color:{theme.text_secondary}; border:none; background:transparent;"
            )
            self.field_grid.addWidget(name_label, row, 0)
            self.field_grid.addWidget(value_label, row, 1)
            self.field_name_labels.append(name_label)
            self.field_value_labels.append(value_label)
        self.root.addLayout(self.field_grid)
        self._finalize()
        self.update_message(message)

    def update_message(self, message: ChatMessage) -> None:
        self.message = message
        payload = message.payload
        self._set_common(payload)
        fields = payload.get("fields", [])
        normalized = fields if isinstance(fields, list) else []
        for index, (name_label, value_label) in enumerate(zip(self.field_name_labels, self.field_value_labels)):
            field = normalized[index] if index < len(normalized) and isinstance(normalized[index], dict) else {}
            label = str(field.get("label", "") or field.get("name", "") or "").strip()
            value = str(field.get("value", "") or "").strip()
            visible = bool(label and value)
            name_label.setText(label)
            value_label.setText(value)
            name_label.setVisible(visible)
            value_label.setVisible(visible)
        self._set_footer(
            str(payload.get("source_url", "") or payload.get("url", "")),
            str(payload.get("source_label", "") or "").strip(),
        )


class CompareCardWidget(_WebCardBase):
    def __init__(self, message: ChatMessage, parent=None, *, language: str = "zh", display_mode: DisplayMode = "normal") -> None:
        theme = resolve_theme(parent)
        super().__init__(
            message,
            parent,
            language=language,
            display_mode=display_mode,
            background=mix(theme.surface, "#FFF3E5", 0.72),
            hover_background=mix(theme.surface, "#FFE7C9", 0.9),
            border=mix(theme.divider, "#D6A15D", 0.28),
            hover_border=mix(theme.divider, "#C57B2D", 0.42),
        )
        self.items_label = QLabel(self)
        self.items_label.setWordWrap(True)
        self.items_label.setStyleSheet(
            f"font-size:{self.mode_metric(11, 10)}px; color:{theme.text_primary}; border:none; background:transparent;"
        )
        self.differences_label = QLabel(self)
        self.differences_label.setWordWrap(True)
        self.differences_label.setStyleSheet(
            f"font-size:{self.mode_metric(11, 10)}px; color:{theme.text_secondary}; border:none; background:transparent;"
        )
        self.recommendation_label = QLabel(self)
        self.recommendation_label.setWordWrap(True)
        self.recommendation_label.setStyleSheet(
            f"font-size:{self.mode_metric(11, 10)}px; font-weight:700; color:{theme.text_primary}; border:none; background:transparent;"
        )
        self.root.addWidget(self.items_label)
        self.root.addWidget(self.differences_label)
        self.root.addWidget(self.recommendation_label)
        self._finalize()
        self.update_message(message)

    def update_message(self, message: ChatMessage) -> None:
        self.message = message
        payload = message.payload
        self._set_common(payload)
        item_lines: list[str] = []
        for item in list(payload.get("items", []) or [])[:3]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "") or "").strip()
            highlights = [str(raw).strip() for raw in list(item.get("highlights", []) or []) if str(raw).strip()]
            if title and highlights:
                item_lines.append(f"{title}: {' / '.join(highlights[:2])}")
            elif title:
                item_lines.append(title)
        difference_lines = [str(raw).strip() for raw in list(payload.get("differences", []) or []) if str(raw).strip()]
        self.items_label.setText("\n".join(item_lines[:3]))
        self.items_label.setVisible(bool(item_lines))
        self.differences_label.setText("\n".join(difference_lines[:3]))
        self.differences_label.setVisible(bool(difference_lines))
        recommendation = str(payload.get("recommendation", "") or "").strip()
        self.recommendation_label.setText(recommendation)
        self.recommendation_label.setVisible(bool(recommendation))
        sources = payload.get("sources", [])
        source_url = ""
        if isinstance(sources, list) and sources and isinstance(sources[0], dict):
            source_url = str(sources[0].get("url", "") or "").strip()
        self._set_footer(
            str(payload.get("source_url", "") or source_url),
            str(payload.get("source_label", "") or "").strip(),
        )


class ReleaseCardWidget(_WebCardBase):
    def __init__(self, message: ChatMessage, parent=None, *, language: str = "zh", display_mode: DisplayMode = "normal") -> None:
        theme = resolve_theme(parent)
        super().__init__(
            message,
            parent,
            language=language,
            display_mode=display_mode,
            background=mix(theme.surface, "#EAFBF1", 0.72),
            hover_background=mix(theme.surface, "#D7F2E5", 0.88),
            border=mix(theme.divider, "#6BB38B", 0.28),
            hover_border=mix(theme.divider, "#388B5E", 0.4),
        )
        self.meta_label = QLabel(self)
        self.meta_label.setWordWrap(True)
        self.meta_label.setStyleSheet(
            f"font-size:{self.mode_metric(11, 10)}px; font-weight:700; color:{theme.text_primary}; border:none; background:transparent;"
        )
        self.highlights_label = QLabel(self)
        self.highlights_label.setWordWrap(True)
        self.highlights_label.setStyleSheet(
            f"font-size:{self.mode_metric(11, 10)}px; color:{theme.text_secondary}; border:none; background:transparent;"
        )
        self.root.addWidget(self.meta_label)
        self.root.addWidget(self.highlights_label)
        self._finalize()
        self.update_message(message)

    def update_message(self, message: ChatMessage) -> None:
        self.message = message
        payload = message.payload
        self._set_common(payload)
        meta_bits = [str(payload.get("date", "") or "").strip(), str(payload.get("status", "") or "").strip()]
        meta_bits = [item for item in meta_bits if item]
        highlights = [str(raw).strip() for raw in list(payload.get("highlights", []) or []) if str(raw).strip()]
        self.meta_label.setText(" | ".join(meta_bits))
        self.meta_label.setVisible(bool(meta_bits))
        self.highlights_label.setText("\n".join(highlights[:4]))
        self.highlights_label.setVisible(bool(highlights))
        self._set_footer(
            str(payload.get("source_url", "") or payload.get("url", "")),
            str(payload.get("source_label", "") or "").strip(),
        )


class WebBriefCardWidget(_WebCardBase):
    def __init__(self, message: ChatMessage, parent=None, *, language: str = "zh", display_mode: DisplayMode = "normal") -> None:
        theme = resolve_theme(parent)
        super().__init__(
            message,
            parent,
            language=language,
            display_mode=display_mode,
            background=mix(theme.surface, "#F4F3EE", 0.78),
            hover_background=mix(theme.surface, "#ECE8DE", 0.9),
            border=mix(theme.divider, "#9C9685", 0.24),
            hover_border=mix(theme.divider, "#6E6A5E", 0.36),
        )
        self.bullets_label = QLabel(self)
        self.bullets_label.setWordWrap(True)
        self.bullets_label.setStyleSheet(
            f"font-size:{self.mode_metric(11, 10)}px; color:{theme.text_secondary}; border:none; background:transparent;"
        )
        self.root.addWidget(self.bullets_label)
        self._finalize()
        self.update_message(message)

    def update_message(self, message: ChatMessage) -> None:
        self.message = message
        payload = message.payload
        self._set_common(payload)
        bullets = [str(raw).strip() for raw in list(payload.get("bullets", []) or []) if str(raw).strip()]
        self.bullets_label.setText("\n".join(f"- {item}" for item in bullets[:4]))
        self.bullets_label.setVisible(bool(bullets))
        self._set_footer(
            str(payload.get("source_url", "") or payload.get("url", "")),
            str(payload.get("source_label", "") or "").strip(),
        )
