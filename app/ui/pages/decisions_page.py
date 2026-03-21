from __future__ import annotations

import html
import json
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from app.ui.components.status_chip import StatusChip
from app.ui.theme import apply_soft_shadow, button_style, card_style, list_widget_style, resolve_theme, tab_widget_style, text_browser_style


class DecisionsPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        theme = resolve_theme(self)
        self.on_action: Callable[[str, str], None] | None = None
        self._items_by_status: dict[str, dict[str, dict[str, Any]]] = {"pending": {}, "confirmed": {}, "rejected": {}}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.pending_chip = StatusChip("0 pending", "pending", self)
        self.confirmed_chip = StatusChip("0 confirmed", "confirmed", self)
        self.rejected_chip = StatusChip("0 rejected", "rejected", self)
        header.addWidget(self.pending_chip)
        header.addWidget(self.confirmed_chip)
        header.addWidget(self.rejected_chip)
        header.addStretch(1)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.tabs = QTabWidget(self)
        self.tabs.setStyleSheet(f"{tab_widget_style(theme)} {list_widget_style(theme)}")
        apply_soft_shadow(self.tabs, theme, blur=18, y_offset=8, strength=0.8)
        self.pending_list = QListWidget(self.tabs)
        self.confirmed_list = QListWidget(self.tabs)
        self.rejected_list = QListWidget(self.tabs)
        self.pending_list.currentItemChanged.connect(lambda cur, prev: self._render_current("pending", cur, prev))
        self.confirmed_list.currentItemChanged.connect(lambda cur, prev: self._render_current("confirmed", cur, prev))
        self.rejected_list.currentItemChanged.connect(lambda cur, prev: self._render_current("rejected", cur, prev))
        self.tabs.currentChanged.connect(lambda _idx: self._update_actions())
        self.tabs.addTab(self.pending_list, "Pending")
        self.tabs.addTab(self.confirmed_list, "Confirmed")
        self.tabs.addTab(self.rejected_list, "Rejected")

        detail = QFrame(self)
        detail.setStyleSheet(
            f"""
            {card_style(theme, 'QFrame', radius=18, soft=False)}
            QLabel {{ color:{theme.text_primary}; }}
            {text_browser_style(theme, background='transparent', border='transparent', padding=0)}
            {button_style(theme)}
            """
        )
        apply_soft_shadow(detail, theme, blur=18, y_offset=8, strength=0.8)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 12, 12, 12)
        detail_layout.setSpacing(8)
        self.detail_title = QLabel("Decision Details", detail)
        self.detail_title.setStyleSheet(f"font-size:15px; font-weight:800; color:{theme.text_primary};")
        self.detail_view = QTextBrowser(detail)
        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self.confirm_button = QPushButton("Confirm", detail)
        self.reject_button = QPushButton("Reject", detail)
        self.details_button = QPushButton("Details", detail)
        self.confirm_button.clicked.connect(lambda: self._emit_action("confirm"))
        self.reject_button.clicked.connect(lambda: self._emit_action("reject"))
        self.details_button.clicked.connect(lambda: self._emit_action("details"))
        button_row.addWidget(self.confirm_button)
        button_row.addWidget(self.reject_button)
        button_row.addWidget(self.details_button)
        button_row.addStretch(1)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addLayout(button_row)
        detail_layout.addWidget(self.detail_view, 1)

        body.addWidget(self.tabs, 1)
        body.addWidget(detail, 1)
        root.addLayout(body, 1)
        self._update_actions()

    def set_snapshot(self, pending: list[dict[str, Any]], confirmed: list[dict[str, Any]], rejected: list[dict[str, Any]]) -> None:
        self._populate(self.pending_list, "pending", pending)
        self._populate(self.confirmed_list, "confirmed", confirmed)
        self._populate(self.rejected_list, "rejected", rejected)
        self.pending_chip.set_chip(f"{len(pending)} pending", "pending" if pending else "inactive")
        self.confirmed_chip.set_chip(f"{len(confirmed)} confirmed", "confirmed" if confirmed else "inactive")
        self.rejected_chip.set_chip(f"{len(rejected)} rejected", "rejected" if rejected else "inactive")
        if self.pending_list.count() > 0 and self.pending_list.currentItem() is None:
            self.pending_list.setCurrentRow(0)
        self._update_actions()

    def select_item(self, item_id: str) -> None:
        for widget in (self.pending_list, self.confirmed_list, self.rejected_list):
            for index in range(widget.count()):
                item = widget.item(index)
                if str(item.data(Qt.UserRole) or "") == item_id:
                    self.tabs.setCurrentWidget(widget)
                    widget.setCurrentItem(item)
                    return

    def _populate(self, widget: QListWidget, status: str, items: list[dict[str, Any]]) -> None:
        widget.clear()
        mapping: dict[str, dict[str, Any]] = {}
        for item in items:
            item_id = str(item.get("id", "") or "")
            title = str(item.get("title", "") or item.get("content", "")[:52] or "Untitled decision")
            stamp = str(item.get("updated_at", "") or item.get("created_at", "") or "")
            list_item = QListWidgetItem(f"{title}\n{stamp}")
            list_item.setData(Qt.UserRole, item_id)
            widget.addItem(list_item)
            mapping[item_id] = dict(item)
        self._items_by_status[status] = mapping

    def _render_current(self, status: str, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        theme = resolve_theme(self)
        if current is None:
            self.detail_title.setText("Decision Details")
            self.detail_view.setHtml(f"<div style='color:{theme.text_secondary};'>Select a decision to inspect it.</div>")
            self._update_actions()
            return
        item_id = str(current.data(Qt.UserRole) or "")
        payload = self._items_by_status.get(status, {}).get(item_id, {})
        title = str(payload.get("title", "") or "Decision")
        self.detail_title.setText(title)
        source_messages = self._json_list(payload.get("source_message_ids_json"))
        tags = self._json_list(payload.get("tags_json"))
        html_text = (
            f"<div style='font-size:13px; color:{theme.text_primary}; font-weight:700; margin-bottom:8px;'>{html.escape(title)}</div>"
            f"<div style='color:{theme.text_secondary}; margin-bottom:10px;'>status={html.escape(str(payload.get('decision_status', status) or status))} - "
            f"importance={html.escape(str(payload.get('importance', '')))} - "
            f"session={html.escape(str(payload.get('source_session_id', '') or '-'))}</div>"
            f"<div style='line-height:1.6; margin-bottom:12px; color:{theme.text_primary};'>{html.escape(str(payload.get('content', '') or '')).replace(chr(10), '<br>')}</div>"
            f"<div style='color:{theme.text_secondary};'>tags: {html.escape(', '.join(tags) or '-')}</div>"
            f"<div style='color:{theme.text_secondary};'>source messages: {html.escape(', '.join(source_messages) or '-')}</div>"
        )
        self.detail_view.setHtml(html_text)
        self._update_actions()

    def _emit_action(self, action: str) -> None:
        if action == "details":
            return
        widget = self.tabs.currentWidget()
        if not isinstance(widget, QListWidget):
            return
        current = widget.currentItem()
        if current is None:
            return
        item_id = str(current.data(Qt.UserRole) or "")
        callback = self.on_action
        if callback is not None and item_id:
            callback(action, item_id)

    def _update_actions(self) -> None:
        if not all(hasattr(self, attr) for attr in ("confirm_button", "reject_button", "details_button")):
            return
        pending_selected = self.tabs.currentWidget() is self.pending_list and self.pending_list.currentItem() is not None
        any_selected = isinstance(self.tabs.currentWidget(), QListWidget) and self.tabs.currentWidget().currentItem() is not None
        self.confirm_button.setEnabled(pending_selected)
        self.reject_button.setEnabled(pending_selected)
        self.details_button.setEnabled(any_selected)

    def _json_list(self, raw: Any) -> list[str]:
        try:
            data = json.loads(str(raw or "[]"))
        except Exception:
            return []
        return [str(item) for item in data if str(item).strip()]
