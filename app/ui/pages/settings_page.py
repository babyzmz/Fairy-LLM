from __future__ import annotations

import html
from typing import Any, Callable

from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from app.ui.i18n import LANG_ZH, normalize_ui_language, tr
from app.ui.theme import apply_soft_shadow, button_style, card_style, resolve_theme, text_browser_style


class SettingsPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        theme = resolve_theme(self)
        self._language = LANG_ZH
        self.on_open_settings: Callable[[], None] | None = None
        self._summary: dict[str, Any] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        card = QFrame(self)
        card.setStyleSheet(
            f"""
            {card_style(theme, 'QFrame', radius=20, soft=False)}
            QLabel {{ color:{theme.text_primary}; }}
            {text_browser_style(theme, background='transparent', border='transparent', padding=0)}
            {button_style(theme)}
            """
        )
        apply_soft_shadow(card, theme, blur=20, y_offset=8, strength=0.8)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        self.title_label = QLabel(card)
        self.title_label.setStyleSheet(f"font-size:18px; font-weight:800; color:{theme.text_primary};")
        self.subtitle_label = QLabel(card)
        self.subtitle_label.setStyleSheet(f"font-size:12px; color:{theme.text_secondary};")
        self.summary_view = QTextBrowser(card)
        self.summary_view.document().setDocumentMargin(0)
        self.open_button = QPushButton(card)
        self.open_button.clicked.connect(self._open_settings)
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)
        layout.addWidget(self.summary_view, 1)
        layout.addWidget(self.open_button)
        root.addWidget(card, 1)

        self.set_ui_language(self._language)

    def set_ui_language(self, language: str) -> None:
        self._language = normalize_ui_language(language)
        self.title_label.setText(tr("settings_title", self._language))
        self.subtitle_label.setText(tr("settings_subtitle", self._language))
        self.open_button.setText(tr("settings_open_full", self._language))
        if self._summary:
            self.set_summary(self._summary)

    def set_summary(self, summary: dict[str, Any]) -> None:
        theme = resolve_theme(self)
        self._summary = dict(summary)
        mode = str(summary.get("mode", "") or "normal_mode")
        provider = str(summary.get("provider", "") or "local_server")
        model = str(summary.get("model", "") or "-")
        vector_backend = str(summary.get("vector_backend", "") or "sqlite")
        fingerprint = str(summary.get("fingerprint", "") or "-")
        rag_enabled = bool(summary.get("rag_enabled", False))
        persona_mode = str(summary.get("persona_mode", "off") or "off")
        html_text = (
            f"<div style='color:{theme.text_primary}; font-weight:700; margin-bottom:8px;'>{tr('settings_current_runtime', self._language)}</div>"
            f"<div>{tr('settings_mode_label', self._language)}: {html.escape(mode)}</div>"
            f"<div>{tr('settings_provider_label', self._language)}: {html.escape(provider)}</div>"
            f"<div>{tr('settings_model_label', self._language)}: {html.escape(model)}</div>"
            f"<div>{tr('settings_backend_label', self._language)}: {html.escape(vector_backend)}</div>"
            f"<div>{tr('settings_fingerprint_label', self._language)}: {html.escape(fingerprint)}</div>"
            f"<div>{tr('settings_rag_label', self._language)}: {html.escape(str(rag_enabled))}</div>"
            f"<div>{tr('settings_persona_label', self._language)}: {html.escape(persona_mode)}</div>"
            f"<div style='margin-top:10px; color:{theme.text_secondary};'>{tr('settings_runtime_hint', self._language)}</div>"
        )
        self.summary_view.setHtml(html_text)

    def _open_settings(self) -> None:
        callback = self.on_open_settings
        if callback is not None:
            callback()
