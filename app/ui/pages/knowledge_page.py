from __future__ import annotations

import html
import json
from typing import Any

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QTextBrowser, QVBoxLayout, QWidget

from app.ui.components.status_chip import StatusChip
from app.ui.theme import apply_soft_shadow, card_style, list_widget_style, resolve_theme, text_browser_style


class KnowledgePage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        theme = resolve_theme(self)
        self._items: dict[str, dict[str, Any]] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self.backend_chip = StatusChip("sqlite", "inactive", self)
        self.fingerprint_chip = StatusChip("no-fingerprint", "inactive", self)
        self.count_chip = StatusChip("0 knowledge items", "info", self)
        metrics.addWidget(self.backend_chip)
        metrics.addWidget(self.fingerprint_chip)
        metrics.addWidget(self.count_chip)
        metrics.addStretch(1)
        root.addLayout(metrics)

        content = QHBoxLayout()
        content.setSpacing(12)
        left = QFrame(self)
        left.setStyleSheet(f"{card_style(theme, 'QFrame', radius=18, soft=False)} {list_widget_style(theme)}")
        apply_soft_shadow(left, theme, blur=18, y_offset=8, strength=0.8)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        left_title = QLabel("Knowledge Browser", left)
        left_title.setStyleSheet(f"font-size:15px; font-weight:800; color:{theme.text_primary};")
        self.list_widget = QListWidget(left)
        self.list_widget.currentItemChanged.connect(self._render_current)
        left_layout.addWidget(left_title)
        left_layout.addWidget(self.list_widget, 1)

        right = QFrame(self)
        right.setStyleSheet(
            f"""
            {card_style(theme, 'QFrame', radius=18, soft=False)}
            {text_browser_style(theme, background='transparent', border='transparent', padding=0)}
            """
        )
        apply_soft_shadow(right, theme, blur=18, y_offset=8, strength=0.8)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(8)
        self.detail_title = QLabel("Details", right)
        self.detail_title.setStyleSheet(f"font-size:15px; font-weight:800; color:{theme.text_primary};")
        self.detail_view = QTextBrowser(right)
        self.detail_view.setOpenExternalLinks(True)
        right_layout.addWidget(self.detail_title)
        right_layout.addWidget(self.detail_view, 1)

        content.addWidget(left, 1)
        content.addWidget(right, 1)
        root.addLayout(content, 1)

    def set_snapshot(self, snapshot: dict[str, Any]) -> None:
        theme = resolve_theme(self)
        items = snapshot.get("items") or []
        total = int(snapshot.get("total", len(items)) or len(items))
        backend = str(snapshot.get("vector_backend", "") or "sqlite")
        fingerprint = str(snapshot.get("active_fingerprint", "") or "no-fingerprint")
        self.backend_chip.set_chip(backend, "inactive")
        self.fingerprint_chip.set_chip(fingerprint[:30], "inactive")
        self.count_chip.set_chip(f"{total} knowledge items", "info")
        self.list_widget.clear()
        self._items.clear()
        for item in items:
            item_id = str(item.get("id", "") or item.get("source_ref_id", "") or "")
            title = str(item.get("title", "") or item.get("memory_type", "") or item.get("source_kind", "Untitled"))
            memory_type = str(item.get("memory_type", "") or item.get("source_kind", "")).strip()
            stamp = str(item.get("updated_at", "") or item.get("created_at", "") or "")
            label = f"{title}\n{memory_type} - {stamp}".strip()
            widget_item = QListWidgetItem(label)
            widget_item.setData(256, item_id)
            self.list_widget.addItem(widget_item)
            self._items[item_id] = dict(item)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        else:
            self.detail_title.setText("Knowledge Details")
            self.detail_view.setHtml(
                f"<div style='color:{theme.text_secondary};'>Knowledge is quiet for now. That usually means the system is staying disciplined.</div>"
            )

    def _render_current(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        theme = resolve_theme(self)
        if current is None:
            return
        item_id = str(current.data(256) or "")
        payload = self._items.get(item_id, {})
        title = str(payload.get("title", "") or payload.get("memory_type", "") or "Knowledge")
        self.detail_title.setText(title)
        tags = self._json_list(payload.get("tags_json"))
        merged_refs = self._json_list(payload.get("merged_source_refs_json"))
        content = html.escape(str(payload.get("content", "") or "")).replace("\n", "<br>")
        html_text = (
            f"<div style='font-size:13px; color:{theme.text_primary}; font-weight:700; margin-bottom:8px;'>{html.escape(title)}</div>"
            f"<div style='color:{theme.text_secondary}; margin-bottom:10px;'>"
            f"type={html.escape(str(payload.get('memory_type', '') or payload.get('source_kind', '')))} - "
            f"importance={html.escape(str(payload.get('importance', '')))} - "
            f"session={html.escape(str(payload.get('source_session_id', '') or '-'))}</div>"
            f"<div style='margin-bottom:12px; line-height:1.6; color:{theme.text_primary};'>{content or '<i>No content.</i>'}</div>"
            f"<div style='color:{theme.text_secondary};'>tags: {html.escape(', '.join(tags) or '-')}</div>"
            f"<div style='color:{theme.text_secondary};'>evidence: {html.escape(str(payload.get('evidence_count', 1) or 1))}</div>"
            f"<div style='color:{theme.text_secondary};'>merged refs: {html.escape(', '.join(merged_refs) or '-')}</div>"
        )
        self.detail_view.setHtml(html_text)

    def _json_list(self, raw: Any) -> list[str]:
        try:
            data = json.loads(str(raw or "[]"))
        except Exception:
            return []
        return [str(item) for item in data if str(item).strip()]
