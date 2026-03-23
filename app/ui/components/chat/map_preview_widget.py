from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.ui.components.chat.base_card_widget import ClickableAsyncImageLabel, HoverCardFrame, open_image_preview, open_url
from app.ui.components.chat.chat_message import ChatMessage
from app.ui.components.chat.message_widget import DisplayMode
from app.ui.theme import button_style, mix, resolve_theme


class MapPreviewWidget(HoverCardFrame):
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
        self._target_url = ""
        self._navigate_url = ""
        self._copy_text = ""
        self._image_source = ""
        self.setMaximumWidth(self.mode_metric(380, 300))
        self.set_palette(
            background=theme.surface,
            hover_background=mix(theme.surface, theme.fairy_blue_soft, 0.06),
            border=mix(theme.divider, theme.fairy_blue_soft, 0.18),
            hover_border=mix(theme.divider, theme.fairy_blue_soft, 0.34),
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(self.mode_metric(14, 12), self.mode_metric(14, 12), self.mode_metric(14, 12), self.mode_metric(14, 12))
        root.setSpacing(self.mode_metric(10, 8))

        self.map_image = ClickableAsyncImageLabel(
            self,
            theme=theme,
            placeholder="Map Preview" if language.startswith("en") else "地图预览",
            minimum_height=self.mode_metric(160, 118),
            corner_radius=16 if display_mode == "normal" else 14,
        )
        self.map_image.set_marker_pin(True)
        self.map_image.clicked.connect(self._open_preview)

        self.title_label = QLabel(self)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(
            f"font-size:{self.mode_metric(14, 12)}px; font-weight:800; color:{theme.text_primary}; border:none; background:transparent;"
        )
        self.subtitle_label = QLabel(self)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setStyleSheet(
            f"font-size:{self.mode_metric(12, 10)}px; color:{theme.text_secondary}; border:none; background:transparent;"
        )
        self.distance_label = QLabel(self)
        self.distance_label.setStyleSheet(
            f"font-size:{self.mode_metric(12, 10)}px; color:{theme.text_secondary}; border:none; background:transparent;"
        )

        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addWidget(self.distance_label, 1)

        self.copy_button = QPushButton("Copy Address" if language.startswith("en") else "复制地址", self)
        self.copy_button.setCursor(Qt.PointingHandCursor)
        self.copy_button.setStyleSheet(button_style(theme, tone="neutral", radius=12, compact=True))
        self.copy_button.clicked.connect(self._copy_address)

        self.navigate_button = QPushButton("Navigate" if language.startswith("en") else "导航", self)
        self.navigate_button.setCursor(Qt.PointingHandCursor)
        self.navigate_button.setStyleSheet(button_style(theme, tone="neutral", radius=12, compact=True))
        self.navigate_button.clicked.connect(self._open_navigation)

        self.open_button = QPushButton("Open in Maps" if language.startswith("en") else "打开地图", self)
        self.open_button.setCursor(Qt.PointingHandCursor)
        self.open_button.setStyleSheet(button_style(theme, tone="accent", radius=12, compact=True))
        self.open_button.clicked.connect(self._open_target)

        footer.addWidget(self.copy_button)
        footer.addWidget(self.navigate_button)
        footer.addWidget(self.open_button)

        root.addWidget(self.map_image)
        root.addWidget(self.title_label)
        root.addWidget(self.subtitle_label)
        root.addLayout(footer)
        self.update_message(message)

    def update_message(self, message: ChatMessage) -> None:
        self.message = message
        payload = message.payload
        title = str(
            payload.get("title", "")
            or payload.get("address", "")
            or ("Map Location" if self.language.startswith("en") else "地图位置")
        ).strip()
        address = str(payload.get("address", "") or title).strip()
        distance = str(payload.get("distance_text", "") or payload.get("distance", "") or payload.get("subtitle", "") or "").strip()
        self._target_url = str(payload.get("external_map_url", "") or payload.get("url", "") or payload.get("map_url", "") or "").strip()
        self._navigate_url = str(payload.get("navigate_url", "") or "").strip() or self._target_url
        self._copy_text = address
        self._image_source = str(
            payload.get("map_preview_path", "")
            or payload.get("image_path", "")
            or payload.get("image", "")
            or payload.get("map_preview_url", "")
            or payload.get("image_url", "")
            or ""
        ).strip()
        if self._image_source and not self._image_source.startswith(("http://", "https://")):
            candidate = Path(self._image_source)
            if not candidate.is_absolute():
                self._image_source = str((Path.cwd() / candidate).resolve())

        self.title_label.setText(title)
        self.subtitle_label.setText(address if address != title else "")
        self.distance_label.setText(distance)
        self.map_image.set_source(self._image_source)
        self.copy_button.setVisible(bool(self._copy_text))
        self.navigate_button.setVisible(bool(self._navigate_url))

    def _open_preview(self) -> None:
        if not self._image_source:
            self._open_target()
            return
        open_image_preview(
            self._image_source,
            title=self.title_label.text(),
            theme=self.theme,
            mark_pin=True,
            parent=self,
        )

    def _open_target(self) -> None:
        open_url(self._target_url)

    def _open_navigation(self) -> None:
        open_url(self._navigate_url)

    def _copy_address(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._copy_text)
