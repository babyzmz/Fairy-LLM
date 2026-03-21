from __future__ import annotations

import html
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from app.ui.theme import apply_soft_shadow, button_style, card_style, resolve_theme, rgba, text_browser_style


class RetrievalDebugPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        theme = resolve_theme(self)
        self._snapshot: dict[str, Any] | None = None
        self.setStyleSheet(
            f"""
            {card_style(theme, 'QWidget', radius=18, soft=True)}
            QLabel {{
                color: {theme.text_primary};
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 0.3px;
            }}
            {text_browser_style(theme, background='transparent', border='transparent', padding=0)}
            {button_style(theme, compact=True)}
            """
        )
        apply_soft_shadow(self, theme, blur=20, y_offset=8, strength=0.8)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self.title_label = QLabel("RAG Retrieval", self)
        self.summary_label = QLabel("", self)
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(f"color:{theme.text_secondary}; font-size:11px; font-weight:500;")

        self.view = QTextBrowser(self)
        self.view.setOpenExternalLinks(True)
        self.view.document().setDocumentMargin(0)

        self.preview_toggle = QPushButton("Show injected context", self)
        self.preview_toggle.setCursor(Qt.PointingHandCursor)
        self.preview_toggle.clicked.connect(self._toggle_preview)

        self.preview_view = QTextBrowser(self)
        self.preview_view.document().setDocumentMargin(0)
        self.preview_view.hide()

        layout.addWidget(self.title_label)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.view)
        layout.addWidget(self.preview_toggle, alignment=Qt.AlignLeft)
        layout.addWidget(self.preview_view)
        self.hide()

    def clear_snapshot(self) -> None:
        self._snapshot = None
        self.summary_label.clear()
        self.view.clear()
        self.preview_view.clear()
        self.preview_view.hide()
        self.preview_toggle.setText("Show injected context")
        self.hide()

    def set_snapshot(self, snapshot: dict[str, Any] | None) -> None:
        theme = resolve_theme(self)
        if not snapshot:
            self.clear_snapshot()
            return
        self._snapshot = snapshot
        hits = snapshot.get("hits") or []
        result_count = int(snapshot.get("result_count", 0) or 0)
        injected_count = int(snapshot.get("injected_context_item_count", 0) or 0)
        truncated = bool(snapshot.get("injected_context_truncated", False))
        fallback_reason = str(snapshot.get("fallback_reason", "") or "").strip()

        summary_parts = [
            f"triggered={snapshot.get('triggered', False)}",
            f"provider={snapshot.get('embedding_provider', '')}",
            f"backend={snapshot.get('vector_backend', '')}",
            f"collection={snapshot.get('collection_name', '')}",
            f"hits={result_count}",
            f"injected={injected_count}",
            f"chars={snapshot.get('injected_context_chars', 0)} / {snapshot.get('max_context_chars', 0)}",
        ]
        if truncated:
            summary_parts.append("truncated=true")
        if fallback_reason:
            summary_parts.append(f"fallback={fallback_reason}")
        self.summary_label.setText(" - ".join(summary_parts))

        lines = []
        if hits:
            lines.append(f"<div style='margin-top:2px; color:{theme.text_primary}; font-weight:700;'>Retrieval results</div>")
            for index, item in enumerate(hits[:8], start=1):
                injected_mark = "yes" if item.get("injected_into_prompt") else "no"
                lines.append(
                    f"<div style='margin:6px 0 0 8px; color:{theme.text_secondary};'>"
                    f"{index}. {html.escape(str(item.get('source_kind', '')))}"
                    f" - score={html.escape(str(item.get('score', '')))}"
                    f" - importance={html.escape(str(item.get('importance', '')))}"
                    f" - injected={injected_mark}"
                    f"<br>{html.escape(str(item.get('title', '') or item.get('source_ref_id', '')))}"
                    "</div>"
                )
        else:
            lines.append(f"<div style='color:{theme.text_secondary};'>No retrieval hits were captured for this turn.</div>")
        self.view.setHtml("".join(lines))

        preview = str(snapshot.get("final_injected_context_preview", "") or "").strip()
        if preview:
            preview_html = html.escape(preview).replace("\n", "<br>")
            self.preview_view.setHtml(
                f"<div style='color:{theme.text_secondary}; margin-bottom:6px;'>"
                f"items={injected_count} - chars={snapshot.get('injected_context_chars', 0)}"
                f" - truncated={truncated}"
                "</div>"
                f"<div style='color:{theme.text_primary}; line-height:1.55;'>{preview_html}</div>"
            )
            self.preview_toggle.show()
        else:
            self.preview_view.setHtml(
                f"<div style='color:{theme.text_secondary};'>No retrieved context was injected for this turn.</div>"
            )
            self.preview_toggle.show()
        self.show()

    def _toggle_preview(self) -> None:
        expanded = not self.preview_view.isVisible()
        self.preview_view.setVisible(expanded)
        self.preview_toggle.setText("Hide injected context" if expanded else "Show injected context")
