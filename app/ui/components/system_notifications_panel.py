from __future__ import annotations

import html
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from app.system_notifications.notification_presenter import (
    can_mark_completed,
    compact_title,
    is_active_notification,
    level_to_tone,
    notification_counts,
    notification_matches_filter,
)
from app.ui.components.status_chip import StatusChip
from app.ui.theme import apply_soft_shadow, button_style, card_style, list_widget_style, resolve_theme, text_browser_style


FILTER_OPTIONS = (
    ("all", "All"),
    ("active", "Active"),
    ("action_required", "Action Required"),
    ("critical", "Critical"),
    ("snoozed", "Snoozed"),
    ("resolved", "Resolved"),
)


class SystemNotificationsPanel(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        theme = resolve_theme(self)
        self.on_action: Callable[[str, str], None] | None = None
        self.on_open_related: Callable[[str, str], None] | None = None
        self._all_items: dict[str, dict[str, Any]] = {}
        self._filter_name = "active"
        self._filter_buttons: dict[str, QPushButton] = {}
        self.setObjectName("systemNotificationsPanel")
        self.setStyleSheet(
            f"""
            {card_style(theme, 'QFrame#systemNotificationsPanel', radius=20, soft=False)}
            QLabel {{ color:{theme.text_primary}; }}
            {list_widget_style(theme)}
            {text_browser_style(theme, background='transparent', border='transparent', padding=0)}
            {button_style(theme)}
            """
        )
        apply_soft_shadow(self, theme, blur=22, y_offset=8, strength=0.85)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        header = QHBoxLayout()
        self.title_label = QLabel("System Tasks", self)
        self.title_label.setStyleSheet(f"font-size:15px; font-weight:800; color:{theme.text_primary};")
        self.count_chip = StatusChip("0 active", "inactive", self)
        self.alert_chip = StatusChip("stable", "inactive", self)
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(self.count_chip)
        header.addWidget(self.alert_chip)
        root.addLayout(header)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        self.filter_summary = QLabel("Default view: Active", self)
        self.filter_summary.setStyleSheet(f"font-size:11px; color:{theme.text_secondary};")
        for name, label in FILTER_OPTIONS:
            button = QPushButton(label, self)
            button.clicked.connect(lambda _checked=False, value=name: self._set_filter(value))
            self._filter_buttons[name] = button
            filter_row.addWidget(button)
        filter_row.addStretch(1)
        filter_row.addWidget(self.filter_summary)
        root.addLayout(filter_row)

        bulk_row = QHBoxLayout()
        bulk_row.setSpacing(6)
        self.bulk_dismiss_button = QPushButton("Dismiss Visible", self)
        self.bulk_complete_button = QPushButton("Complete Visible", self)
        self.bulk_snooze_1h_button = QPushButton("Snooze 1h", self)
        self.bulk_snooze_tomorrow_button = QPushButton("Snooze Tomorrow", self)
        self.bulk_snooze_1d_button = QPushButton("Snooze 1d", self)
        self.bulk_clear_non_critical_button = QPushButton("Clear Non-Critical", self)
        self.bulk_dismiss_button.clicked.connect(lambda: self._emit_bulk_action("dismiss_many", self._visible_active_ids()))
        self.bulk_complete_button.clicked.connect(lambda: self._emit_bulk_action("complete_many", self._visible_completable_ids()))
        self.bulk_snooze_1h_button.clicked.connect(lambda: self._emit_bulk_action("snooze_many_1h", self._visible_active_ids()))
        self.bulk_snooze_tomorrow_button.clicked.connect(lambda: self._emit_bulk_action("snooze_many_tomorrow", self._visible_active_ids()))
        self.bulk_snooze_1d_button.clicked.connect(lambda: self._emit_bulk_action("snooze_many_1d", self._visible_active_ids()))
        self.bulk_clear_non_critical_button.clicked.connect(lambda: self._emit_bulk_action("dismiss_many", self._visible_non_critical_ids()))
        for button in (
            self.bulk_dismiss_button,
            self.bulk_complete_button,
            self.bulk_snooze_1h_button,
            self.bulk_snooze_tomorrow_button,
            self.bulk_snooze_1d_button,
            self.bulk_clear_non_critical_button,
        ):
            bulk_row.addWidget(button)
        bulk_row.addStretch(1)
        root.addLayout(bulk_row)

        body = QHBoxLayout()
        body.setSpacing(10)
        self.list_widget = QListWidget(self)
        self.list_widget.currentItemChanged.connect(self._render_current)
        body.addWidget(self.list_widget, 1)

        detail = QWidget(self)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(8)
        self.detail_title = QLabel("Notification Details", detail)
        self.detail_title.setStyleSheet(f"font-size:14px; font-weight:700; color:{theme.text_primary};")
        button_row = QHBoxLayout()
        self.dismiss_button = QPushButton("Dismiss", detail)
        self.complete_button = QPushButton("Complete", detail)
        self.snooze_button = QPushButton("Snooze 4h", detail)
        self.open_button = QPushButton("Open Related", detail)
        self.dismiss_button.clicked.connect(lambda: self._emit_action("dismiss"))
        self.complete_button.clicked.connect(lambda: self._emit_action("complete"))
        self.snooze_button.clicked.connect(lambda: self._emit_action("snooze"))
        self.open_button.clicked.connect(self._emit_open_related)
        button_row.addWidget(self.dismiss_button)
        button_row.addWidget(self.complete_button)
        button_row.addWidget(self.snooze_button)
        button_row.addWidget(self.open_button)
        button_row.addStretch(1)
        self.detail_view = QTextBrowser(detail)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addLayout(button_row)
        detail_layout.addWidget(self.detail_view, 1)
        body.addWidget(detail, 1)
        root.addLayout(body, 1)
        self._set_filter("active")

    def set_notifications(self, items: list[dict[str, Any]]) -> None:
        self._all_items = {str(item.get("id", "") or ""): dict(item) for item in items}
        self._refresh_list()

    def _set_filter(self, name: str) -> None:
        theme = resolve_theme(self)
        self._filter_name = name
        for filter_name, button in self._filter_buttons.items():
            active = filter_name == name
            button.setStyleSheet(button_style(theme, tone="accent" if active else "neutral", compact=True))
        self._refresh_list()

    def _refresh_list(self) -> None:
        theme = resolve_theme(self)
        items = self._filtered_items()
        counts = notification_counts(list(self._all_items.values()))
        self.list_widget.clear()
        for item in items:
            notification_id = str(item.get("id", "") or "")
            message = str(item.get("message", "") or "").strip()
            preview = message[:80] + ("..." if len(message) > 80 else "")
            list_item = QListWidgetItem(f"{compact_title(_NotificationProxy(item))}\n{preview}")
            list_item.setData(Qt.UserRole, notification_id)
            self.list_widget.addItem(list_item)

        active_count = counts.get("active", 0)
        action_required_count = counts.get("action_required", 0)
        critical_count = counts.get("critical", 0)
        self.count_chip.set_chip(f"{len(items)} visible", "pending" if items else "inactive")
        if critical_count > 0:
            self.alert_chip.set_chip(f"{critical_count} critical", "error")
        elif action_required_count > 0:
            self.alert_chip.set_chip(f"{action_required_count} action", "pending")
        elif active_count > 0:
            self.alert_chip.set_chip(f"{active_count} active", "warning")
        else:
            self.alert_chip.set_chip("stable", "inactive")
        self.filter_summary.setText(
            f"Filter={self._filter_name.replace('_', ' ').title()} - active={active_count} - action={action_required_count} - critical={critical_count}"
        )

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        else:
            self.detail_title.setText("System Tasks")
            self.detail_view.setHtml(f"<div style='color:{theme.text_secondary};'>No notifications match the current filter.</div>")
        self._update_buttons()

    def _filtered_items(self) -> list[dict[str, Any]]:
        items = list(self._all_items.values())
        items.sort(key=lambda item: str(item.get("updated_at", "") or item.get("created_at", "") or ""), reverse=True)
        return [item for item in items if notification_matches_filter(item, self._filter_name)]

    def _visible_active_ids(self) -> list[str]:
        return [
            str(item.get("id", "") or "")
            for item in self._filtered_items()
            if is_active_notification(item)
        ]

    def _visible_non_critical_ids(self) -> list[str]:
        return [
            str(item.get("id", "") or "")
            for item in self._filtered_items()
            if is_active_notification(item) and str(item.get("level", "") or "") != "critical"
        ]

    def _visible_completable_ids(self) -> list[str]:
        return [
            str(item.get("id", "") or "")
            for item in self._filtered_items()
            if is_active_notification(item) and can_mark_completed(item)
        ]

    def _render_current(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        theme = resolve_theme(self)
        if current is None:
            self.detail_title.setText("Notification Details")
            self.detail_view.setHtml(f"<div style='color:{theme.text_secondary};'>Select a system notification to inspect it.</div>")
            self._update_buttons()
            return
        notification_id = str(current.data(Qt.UserRole) or "")
        item = self._all_items.get(notification_id, {})
        metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
        tone = level_to_tone(str(item.get("level", "") or "info"))
        state_bits = []
        if bool(item.get("is_completed", False)):
            state_bits.append("completed")
        if bool(item.get("is_dismissed", False)):
            state_bits.append("dismissed")
        if str(item.get("snooze_until", "") or "").strip():
            state_bits.append(f"snoozed_until={item.get('snooze_until', '')}")
        state_label = " - ".join(state_bits) if state_bits else "active"
        self.detail_title.setText(str(item.get("title", "") or "Notification"))
        metadata_html = "".join(
            f"<li>{html.escape(str(key))}: {html.escape(str(value))}</li>"
            for key, value in metadata.items()
            if str(value).strip()
        ) or "<li>No additional metadata.</li>"
        self.detail_view.setHtml(
            f"<div><b>Level</b>: <span style='color:{self._tone_color(tone)}'>{html.escape(str(item.get('level', '') or 'info'))}</span></div>"
            f"<div><b>State</b>: {html.escape(state_label)}</div>"
            f"<div style='margin-top:6px; line-height:1.6; color:{theme.text_primary};'>{html.escape(str(item.get('message', '') or ''))}</div>"
            f"<div style='margin-top:8px; color:{theme.text_secondary};'>created_at: {html.escape(str(item.get('created_at', '') or '-'))}</div>"
            f"<div style='margin-top:12px; color:{theme.text_primary}; font-weight:700;'>Metadata</div><ul>{metadata_html}</ul>"
        )
        self._update_buttons()

    def _emit_action(self, action: str) -> None:
        current = self.list_widget.currentItem()
        if current is None:
            return
        notification_id = str(current.data(Qt.UserRole) or "")
        callback = self.on_action
        if callback is not None and notification_id:
            callback(action, notification_id)

    def _emit_bulk_action(self, action: str, notification_ids: list[str]) -> None:
        callback = self.on_action
        if callback is None or not notification_ids:
            return
        callback(action, ",".join(notification_ids))

    def _emit_open_related(self) -> None:
        current = self.list_widget.currentItem()
        if current is None:
            return
        item = self._all_items.get(str(current.data(Qt.UserRole) or ""), {})
        callback = self.on_open_related
        if callback is not None:
            callback(str(item.get("related_entity_type", "") or ""), str(item.get("related_entity_id", "") or ""))

    def _update_buttons(self) -> None:
        current = self.list_widget.currentItem()
        enabled = current is not None
        item = self._all_items.get(str(current.data(Qt.UserRole) or ""), {}) if enabled else {}
        self.dismiss_button.setEnabled(enabled and is_active_notification(item))
        self.complete_button.setEnabled(enabled and is_active_notification(item) and can_mark_completed(item))
        self.snooze_button.setEnabled(enabled and is_active_notification(item))
        self.open_button.setEnabled(enabled)

        visible_active = self._visible_active_ids()
        visible_completable = self._visible_completable_ids()
        visible_non_critical = self._visible_non_critical_ids()
        self.bulk_dismiss_button.setEnabled(bool(visible_active))
        self.bulk_complete_button.setEnabled(bool(visible_completable))
        self.bulk_snooze_1h_button.setEnabled(bool(visible_active))
        self.bulk_snooze_tomorrow_button.setEnabled(bool(visible_active))
        self.bulk_snooze_1d_button.setEnabled(bool(visible_active))
        self.bulk_clear_non_critical_button.setEnabled(bool(visible_non_critical))

    def _tone_color(self, tone: str) -> str:
        theme = resolve_theme(self)
        return {
            "error": theme.error,
            "warning": theme.warning,
            "pending": theme.warning,
            "success": theme.success,
            "info": theme.fairy_blue_core,
        }.get(tone, theme.fairy_blue_core)


class _NotificationProxy:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.level = str(payload.get("level", "") or "info")
        self.title = str(payload.get("title", "") or "")
