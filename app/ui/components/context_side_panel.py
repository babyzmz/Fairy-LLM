from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QTextBrowser, QVBoxLayout

from app.ui.theme import apply_soft_shadow, card_style, resolve_theme, text_browser_style


class ContextSidePanel(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        theme = resolve_theme(self)
        self.setObjectName("contextSidePanel")
        self.setMinimumWidth(280)
        self.setMaximumWidth(360)
        self.setStyleSheet(
            f"""
            {card_style(theme, "QFrame#contextSidePanel", radius=20, soft=True)}
            QLabel {{
                color: {theme.text_primary};
            }}
            {text_browser_style(theme, background='transparent', border='transparent', padding=0)}
            """
        )
        apply_soft_shadow(self, theme, blur=24, y_offset=8, strength=0.9)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        self.title_label = QLabel("Context", self)
        self.title_label.setStyleSheet(f"font-size:15px; font-weight:800; color:{theme.text_primary};")
        self.subtitle_label = QLabel("Auxiliary context stays here.", self)
        self.subtitle_label.setStyleSheet(f"font-size:11px; color:{theme.text_secondary};")
        self.view = QTextBrowser(self)
        self.view.setOpenExternalLinks(True)
        self.view.document().setDocumentMargin(0)

        root.addWidget(self.title_label)
        root.addWidget(self.subtitle_label)
        root.addWidget(self.view, 1)

    def set_context(self, title: str, subtitle: str, html_text: str) -> None:
        theme = resolve_theme(self)
        self.title_label.setText(title or "Context")
        self.subtitle_label.setText(subtitle or "")
        self.view.setHtml(
            html_text or f"<div style='color:{theme.text_secondary};'>No additional context for this page.</div>"
        )
