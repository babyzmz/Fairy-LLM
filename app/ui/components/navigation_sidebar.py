from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.ui.components.status_chip import StatusChip
from app.ui.i18n import LANG_ZH, normalize_ui_language, tr
from app.ui.theme import apply_soft_shadow, card_style, resolve_theme, rgba


PAGE_ITEMS = [
    ("chat", "chat_page"),
    ("knowledge", "knowledge_page"),
    ("decisions", "decisions_page"),
    ("jobs", "jobs_page"),
    ("debug", "debug_page"),
    ("settings", "settings_page"),
]


class NavigationSidebar(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        theme = resolve_theme(self)
        self._language = LANG_ZH
        self._buttons: dict[str, QPushButton] = {}
        self._badges: dict[str, StatusChip] = {}
        self._current_page = "chat"
        self.on_page_selected: Callable[[str], None] | None = None
        self.setObjectName("navigationSidebar")
        self.setStyleSheet(
            f"""
            {card_style(theme, "QFrame#navigationSidebar", radius=20, soft=True)}
            QLabel {{
                color: {theme.text_primary};
            }}
            QPushButton {{
                color: {theme.text_secondary};
                background: transparent;
                border: 1px solid transparent;
                border-radius: 15px;
                padding: 11px 13px;
                text-align: left;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                color: {theme.text_primary};
                background: {rgba(theme.surface_soft, 210)};
                border: 1px solid {rgba(theme.divider, 230)};
            }}
            QPushButton[active="true"] {{
                color: {theme.text_primary};
                background: {rgba(theme.surface, 248)};
                border: 1px solid {rgba(theme.fairy_blue_soft, 188)};
            }}
            """
        )
        apply_soft_shadow(self, theme, blur=26, y_offset=8, strength=1.0)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 18, 14, 14)
        root.setSpacing(14)

        brand = QVBoxLayout()
        brand.setSpacing(3)
        self.brand_title = QLabel("Fairy", self)
        self.brand_title.setStyleSheet(f"font-size:26px; font-weight:800; color:{theme.text_primary};")
        self.brand_subtitle = QLabel("", self)
        self.brand_subtitle.setStyleSheet(
            f"font-size:11px; letter-spacing:0.8px; color:{theme.text_secondary}; text-transform:uppercase;"
        )
        brand.addWidget(self.brand_title)
        brand.addWidget(self.brand_subtitle)
        root.addLayout(brand)

        self.hero_slot = QVBoxLayout()
        self.hero_slot.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self.hero_slot)

        nav = QVBoxLayout()
        nav.setSpacing(8)
        for key, text_key in PAGE_ITEMS:
            row = QWidget(self)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            button = QPushButton(row)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, page=key: self._select(page))
            button.setText(tr(text_key, self._language))
            badge = StatusChip("", "inactive", row)
            badge.hide()
            badge.setMinimumWidth(22)
            row_layout.addWidget(button, 1)
            row_layout.addWidget(badge, 0, Qt.AlignRight)
            nav.addWidget(row)
            self._buttons[key] = button
            self._badges[key] = badge
        nav.addStretch(1)
        root.addLayout(nav, 1)

        self.footer_label = QLabel("", self)
        self.footer_label.setStyleSheet(f"font-size:11px; color:{theme.text_secondary};")
        self.footer_label.setWordWrap(True)
        root.addWidget(self.footer_label)
        self.set_ui_language(self._language)
        self.set_current_page("chat")

    def set_ui_language(self, language: str) -> None:
        self._language = normalize_ui_language(language)
        self.brand_subtitle.setText(tr("app_subtitle", self._language))
        self.footer_label.setText(tr("nav_footer", self._language))
        for key, text_key in PAGE_ITEMS:
            button = self._buttons.get(key)
            if button is not None:
                button.setText(tr(text_key, self._language))

    def set_header_widget(self, widget: QWidget) -> None:
        while self.hero_slot.count():
            item = self.hero_slot.takeAt(0)
            existing = item.widget()
            if existing is not None:
                existing.setParent(None)
        if widget is not None:
            self.hero_slot.addWidget(widget, 0, Qt.AlignHCenter)

    def set_current_page(self, page: str) -> None:
        self._current_page = page
        for key, button in self._buttons.items():
            active = key == page
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)

    def set_badge_count(self, page: str, count: int, *, tone: str = "pending") -> None:
        badge = self._badges.get(page)
        if badge is None:
            return
        if count <= 0:
            badge.hide()
            return
        badge.set_chip(str(count), tone)
        badge.show()

    def _select(self, page: str) -> None:
        self.set_current_page(page)
        callback = self.on_page_selected
        if callback is not None:
            callback(page)
