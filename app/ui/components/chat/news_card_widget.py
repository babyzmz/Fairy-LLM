from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.ui.components.chat.base_card_widget import ClickableAsyncImageLabel, ChipLabel, HoverCardFrame, extract_domain, open_url
from app.ui.components.chat.message_widget import DisplayMode
from app.ui.theme import button_style, mix, resolve_theme


class NewsItemCardWidget(HoverCardFrame):
    def __init__(
        self,
        item: dict[str, Any],
        parent=None,
        *,
        language: str = "zh",
        display_mode: DisplayMode = "normal",
    ) -> None:
        theme = resolve_theme(parent)
        super().__init__(parent, theme=theme, radius=16 if display_mode == "normal" else 14, interactive=True, display_mode=display_mode)
        self.item = dict(item)
        self.language = language
        self._target_url = ""
        self.set_palette(
            background=theme.surface,
            hover_background=mix(theme.surface, theme.fairy_blue_soft, 0.06),
            border=mix(theme.divider, theme.fairy_blue_soft, 0.16),
            hover_border=mix(theme.divider, theme.fairy_blue_soft, 0.34),
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(self.mode_metric(12, 10), self.mode_metric(12, 10), self.mode_metric(12, 10), self.mode_metric(12, 10))
        root.setSpacing(self.mode_metric(8, 6))

        self.image_label = ClickableAsyncImageLabel(
            self,
            theme=theme,
            placeholder="Preview" if language.startswith("en") else "预览",
            minimum_height=self.mode_metric(108, 92),
            corner_radius=14 if display_mode == "normal" else 12,
        )
        self.image_label.clicked.connect(self._open_target)

        self.title_label = QLabel(self)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(
            f"font-size:{self.mode_metric(13, 12)}px; font-weight:800; color:{theme.text_primary}; border:none; background:transparent;"
        )
        self.summary_label = QLabel(self)
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(
            f"font-size:{self.mode_metric(12, 10)}px; color:{theme.text_secondary}; border:none; background:transparent;"
        )

        self.tag_row = QHBoxLayout()
        self.tag_row.setSpacing(6)
        self.tag_row.addStretch(1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.domain_label = QLabel(self)
        self.domain_label.setStyleSheet(
            f"font-size:{self.mode_metric(11, 10)}px; font-weight:700; color:{theme.text_secondary}; border:none; background:transparent;"
        )
        self.open_button = QPushButton("Open" if language.startswith("en") else "打开", self)
        self.open_button.setCursor(Qt.PointingHandCursor)
        self.open_button.setStyleSheet(button_style(theme, tone="accent", radius=12, compact=True))
        self.open_button.clicked.connect(self._open_target)
        footer.addWidget(self.domain_label, 1)
        footer.addWidget(self.open_button)

        root.addWidget(self.image_label)
        root.addWidget(self.title_label)
        root.addWidget(self.summary_label)
        root.addLayout(self.tag_row)
        root.addLayout(footer)
        self.update_item(self.item)

    def update_item(self, item: dict[str, Any]) -> None:
        self.item = dict(item)
        theme = self.theme
        self._target_url = str(item.get("url", "") or "").strip()
        title = str(item.get("headline", "") or item.get("title", "") or "").strip()
        summary = str(item.get("summary", "") or item.get("short_comment", "") or "").strip()
        image = str(
            item.get("image_path", "")
            or item.get("image_url", "")
            or item.get("image", "")
            or item.get("thumbnail", "")
            or ""
        ).strip()
        if image and not image.startswith(("http://", "https://")):
            candidate = Path(image)
            if not candidate.is_absolute():
                image = str((Path.cwd() / candidate).resolve())
        source = str(item.get("source", "") or "").strip()
        published_at = str(item.get("published_at", "") or "").strip()
        self.title_label.setText(title)
        self.summary_label.setText(summary)
        footer_bits = [bit for bit in (source, extract_domain(self._target_url), published_at) if bit]
        self.domain_label.setText(" • ".join(footer_bits[:2]) if footer_bits else extract_domain(self._target_url))
        self.image_label.setVisible(bool(image))
        if image:
            self.image_label.set_source(image)

        while self.tag_row.count():
            item_layout = self.tag_row.takeAt(0)
            widget = item_layout.widget()
            if widget is not None:
                widget.deleteLater()
        for tag in list(item.get("tags", []) or [])[: (2 if self.is_compact() else 3)]:
            self.tag_row.addWidget(ChipLabel(str(tag), self, theme=theme))
        self.tag_row.addStretch(1)

    def _open_target(self) -> None:
        open_url(self._target_url)
