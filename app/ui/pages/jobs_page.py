from __future__ import annotations

import html
import json
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QPushButton, QTabWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget

from app.rag.reindex_job import ReindexJob
from app.ui.components.status_chip import StatusChip
from app.ui.theme import (
    apply_soft_shadow,
    button_style,
    card_style,
    list_widget_style,
    resolve_theme,
    tab_widget_style,
    text_browser_style,
    tone_colors,
)


def build_health_check_rows(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    summary = dict(summary or {})
    processed_ratio = float(summary.get("processed_ratio", 0.0) or 0.0)
    failed_ratio = float(summary.get("failed_ratio", 0.0) or 0.0)
    return [
        {"label": "Processed ratio", "passed": processed_ratio >= 0.98, "value": f"{processed_ratio:.2%}"},
        {"label": "Failed ratio", "passed": failed_ratio <= 0.02, "value": f"{failed_ratio:.2%}"},
        {
            "label": "Collection readable",
            "passed": bool(summary.get("collection_readable", False)),
            "value": "yes" if bool(summary.get("collection_readable", False)) else "no",
        },
        {
            "label": "Vector dim match",
            "passed": bool(summary.get("vector_dim_match", False)),
            "value": "yes" if bool(summary.get("vector_dim_match", False)) else "no",
        },
        {
            "label": "Sample lookup",
            "passed": bool(summary.get("sample_lookup_passed", False)),
            "value": "yes" if bool(summary.get("sample_lookup_passed", False)) else "no",
        },
        {
            "label": "Query smoke test",
            "passed": bool(summary.get("query_smoke_test_passed", False)),
            "value": "yes" if bool(summary.get("query_smoke_test_passed", False)) else "no",
        },
    ]


def build_health_check_overview(summary: dict[str, Any] | None) -> dict[str, Any]:
    rows = build_health_check_rows(summary)
    warnings = list((summary or {}).get("warnings") or [])
    reasons = list((summary or {}).get("reasons") or [])
    passed_count = sum(1 for row in rows if row["passed"])
    return {
        "rows": rows,
        "passed_count": passed_count,
        "failed_count": len(rows) - passed_count,
        "warnings_count": len(warnings),
        "reasons_count": len(reasons),
        "overall_passed": bool((summary or {}).get("passed", False)),
        "warnings": warnings,
        "reasons": reasons,
    }


def build_generation_lineage(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(payload: dict[str, Any]) -> str:
        return str(payload.get("promoted_at", "") or payload.get("finished_at", "") or payload.get("created_at", "") or "")

    ordered = [dict(item) for item in items]
    ordered.sort(key=sort_key, reverse=True)
    active_job_id = ""
    for payload in ordered:
        if bool(payload.get("is_active")):
            active_job_id = str(payload.get("job_id", "") or "")
            break
    previous_marked = False
    lineage: list[dict[str, Any]] = []
    for payload in ordered:
        job_id = str(payload.get("job_id", "") or "")
        is_active = bool(payload.get("is_active"))
        if is_active:
            generation_role = "current"
        elif not previous_marked and job_id != active_job_id:
            generation_role = "previous"
            previous_marked = True
        else:
            generation_role = "history"
        lineage.append(
            {
                "job_id": job_id,
                "target_fingerprint": str(payload.get("target_fingerprint", "") or ""),
                "provider": str(payload.get("target_provider", "") or ""),
                "model": str(payload.get("target_model", "") or ""),
                "backend": str(payload.get("backend_type", "") or ""),
                "promoted_at": sort_key(payload),
                "is_active": is_active,
                "parent_job_id": str((payload.get("metadata") or {}).get("parent_job_id", "") or ""),
                "retry_mode": str((payload.get("metadata") or {}).get("retry_mode", "") or ""),
                "generation_role": generation_role,
            }
        )
    return lineage


def _status_badge(label: str, tone: str) -> str:
    theme = resolve_theme()
    fg, bg, border = tone_colors(theme, tone)
    return (
        "<span style='display:inline-block; padding:2px 8px; margin-right:6px; "
        f"color:{fg}; background:{bg}; border:1px solid {border}; border-radius:999px; "
        "font-size:11px; font-weight:700;'>"
        f"{html.escape(label)}</span>"
    )


class JobsPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        theme = resolve_theme(self)
        self.on_job_action: Callable[[str, str], None] | None = None
        self.on_reindex_requested: Callable[[], None] | None = None
        self._jobs: dict[str, dict[str, Any]] = {}
        self._promoted_jobs: dict[str, dict[str, Any]] = {}
        self._lineage: list[dict[str, Any]] = []
        self._show_raw_health = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.running_chip = StatusChip("0 running", "running", self)
        self.healthy_chip = StatusChip("0 eligible", "success", self)
        self.promoted_chip = StatusChip("0 promoted", "active", self)
        self.rebuild_button = QPushButton("Rebuild Embeddings", self)
        self.rebuild_button.setCursor(Qt.PointingHandCursor)
        self.rebuild_button.setStyleSheet(button_style(theme))
        self.rebuild_button.clicked.connect(self._request_reindex)
        header.addWidget(self.running_chip)
        header.addWidget(self.healthy_chip)
        header.addWidget(self.promoted_chip)
        header.addStretch(1)
        header.addWidget(self.rebuild_button)
        root.addLayout(header)

        self.lineage_view = QTextBrowser(self)
        self.lineage_view.setOpenExternalLinks(False)
        self.lineage_view.setStyleSheet(text_browser_style(theme, radius=18))
        apply_soft_shadow(self.lineage_view, theme, blur=18, y_offset=8, strength=0.8)
        root.addWidget(self.lineage_view)

        body = QHBoxLayout()
        body.setSpacing(12)

        self.tabs = QTabWidget(self)
        self.tabs.setStyleSheet(f"{tab_widget_style(theme)} {list_widget_style(theme)}")
        apply_soft_shadow(self.tabs, theme, blur=18, y_offset=8, strength=0.8)
        self.jobs_list = QListWidget(self.tabs)
        self.jobs_list.currentItemChanged.connect(lambda cur, prev: self._render_current("jobs", cur, prev))
        self.promoted_list = QListWidget(self.tabs)
        self.promoted_list.currentItemChanged.connect(lambda cur, prev: self._render_current("promoted", cur, prev))
        self.tabs.addTab(self.jobs_list, "Jobs")
        self.tabs.addTab(self.promoted_list, "Promoted History")
        self.tabs.currentChanged.connect(lambda _idx: self._update_buttons())
        body.addWidget(self.tabs, 1)

        detail = QFrame(self)
        detail.setStyleSheet(
            f"""
            {card_style(theme, 'QFrame', radius=18, soft=False)}
            QLabel {{ color:{theme.text_primary}; }}
            {text_browser_style(theme, background='transparent', border='transparent', padding=0)}
            {button_style(theme)}
            QToolButton {{
                color:{theme.text_primary};
                background: transparent;
                border: 1px solid {theme.divider};
                border-radius:12px;
                padding:8px 14px;
                font-weight:600;
            }}
            """
        )
        apply_soft_shadow(detail, theme, blur=18, y_offset=8, strength=0.8)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 12, 12, 12)
        detail_layout.setSpacing(8)
        self.detail_title = QLabel("Job Details", detail)
        self.detail_title.setStyleSheet(f"font-size:15px; font-weight:800; color:{theme.text_primary};")
        button_row = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel", detail)
        self.promote_button = QPushButton("Promote", detail)
        self.retry_button = QToolButton(detail)
        self.retry_button.setText("Retry Failed Chunks")
        self.retry_button.setPopupMode(QToolButton.MenuButtonPopup)
        self.retry_menu = QMenu(self.retry_button)
        self.retry_all_action = QAction("Retry All Failed Chunks", self.retry_menu)
        self.retry_embedding_action = QAction("Retry Embedding Provider Errors", self.retry_menu)
        self.retry_backend_action = QAction("Retry Backend Errors", self.retry_menu)
        self.retry_all_action.triggered.connect(lambda: self._emit_job_action("retry_failed"))
        self.retry_embedding_action.triggered.connect(lambda: self._emit_job_action("retry_failed_embedding"))
        self.retry_backend_action.triggered.connect(lambda: self._emit_job_action("retry_failed_backend"))
        self.retry_menu.addAction(self.retry_all_action)
        self.retry_menu.addAction(self.retry_embedding_action)
        self.retry_menu.addAction(self.retry_backend_action)
        self.retry_menu.setStyleSheet(
            f"""
            QMenu {{
                background: {theme.surface};
                color: {theme.text_primary};
                border: 1px solid {theme.divider};
                border-radius: 12px;
                padding: 6px;
            }}
            QMenu::item {{
                padding: 8px 12px;
                border-radius: 8px;
            }}
            QMenu::item:selected {{
                background: {theme.surface_soft};
            }}
            """
        )
        self.retry_button.setMenu(self.retry_menu)
        self.raw_toggle_button = QPushButton("Raw Health JSON", detail)
        self.cancel_button.clicked.connect(lambda: self._emit_job_action("cancel"))
        self.promote_button.clicked.connect(lambda: self._emit_job_action("promote"))
        self.retry_button.clicked.connect(lambda: self._emit_job_action("retry_failed"))
        self.raw_toggle_button.clicked.connect(self._toggle_raw_health)
        for button in (self.cancel_button, self.promote_button, self.retry_button, self.raw_toggle_button):
            button_row.addWidget(button)
        button_row.addStretch(1)
        self.detail_view = QTextBrowser(detail)
        self.detail_view.setOpenExternalLinks(False)
        self.raw_health_view = QTextBrowser(detail)
        self.raw_health_view.setStyleSheet(text_browser_style(theme, radius=14))
        self.raw_health_view.hide()
        detail_layout.addWidget(self.detail_title)
        detail_layout.addLayout(button_row)
        detail_layout.addWidget(self.detail_view, 1)
        detail_layout.addWidget(self.raw_health_view, 1)

        body.addWidget(detail, 1)
        root.addLayout(body, 1)
        self._update_buttons()

    def set_jobs(self, items: list[dict[str, Any]]) -> None:
        theme = resolve_theme(self)
        self.jobs_list.clear()
        self._jobs = {}
        running_count = 0
        eligible_count = 0
        for item in items:
            payload = dict(item)
            job_id = str(payload.get("job_id", "") or "")
            status = str(payload.get("status", "") or "")
            if status in {"queued", "running", "cancel_requested"}:
                running_count += 1
            if str(payload.get("promotion_status", "") or "") == "eligible":
                eligible_count += 1
            list_item = QListWidgetItem(self._build_job_label(payload))
            list_item.setData(Qt.UserRole, job_id)
            self.jobs_list.addItem(list_item)
            self._jobs[job_id] = payload
        self.running_chip.set_chip(f"{running_count} running", "running" if running_count else "inactive")
        self.healthy_chip.set_chip(f"{eligible_count} eligible", "success" if eligible_count else "inactive")
        if self.jobs_list.count() > 0 and self.jobs_list.currentItem() is None:
            self.jobs_list.setCurrentRow(0)
        elif self.jobs_list.count() == 0 and self.tabs.currentWidget() is self.jobs_list:
            self.detail_title.setText("Job Details")
            self.detail_view.setHtml(f"<div style='color:{theme.text_secondary};'>No reindex jobs yet. Quiet is healthy until you need a migration.</div>")
        self._update_buttons()

    def set_promoted_history(self, items: list[dict[str, Any]]) -> None:
        self.promoted_list.clear()
        self._promoted_jobs = {}
        for item in items:
            payload = dict(item)
            job_id = str(payload.get("job_id", "") or "")
            list_item = QListWidgetItem(self._build_history_label(payload))
            list_item.setData(Qt.UserRole, job_id)
            self.promoted_list.addItem(list_item)
            self._promoted_jobs[job_id] = payload
        self._lineage = build_generation_lineage(items)
        self._render_lineage()
        self.promoted_chip.set_chip(f"{len(items)} promoted", "active" if items else "inactive")
        self._update_buttons()

    def select_job(self, job_id: str) -> None:
        for widget in (self.jobs_list, self.promoted_list):
            for index in range(widget.count()):
                item = widget.item(index)
                if str(item.data(Qt.UserRole) or "") == job_id:
                    self.tabs.setCurrentWidget(widget)
                    widget.setCurrentItem(item)
                    return

    def _build_job_label(self, payload: dict[str, Any]) -> str:
        status = str(payload.get("status", "") or "")
        source = str(payload.get("source_fingerprint", "") or "-")
        target = str(payload.get("target_fingerprint", "") or "-")
        provider = str(payload.get("target_provider", "") or "provider")
        model = str(payload.get("target_model", "") or "model")
        prefix: list[str] = []
        if bool(payload.get("is_active")):
            prefix.append("[ACTIVE]")
        if str(payload.get("promotion_status", "") or "") == "promoted":
            prefix.append("[PROMOTED]")
        metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
        if metadata.get("retry_mode"):
            prefix.append("[RETRY]")
        head = " ".join(prefix + [status]).strip()
        return f"{head}\n{provider} / {model}\n{source} -> {target}"

    def _build_history_label(self, payload: dict[str, Any]) -> str:
        promoted_at = str(payload.get("promoted_at", "") or payload.get("finished_at", "") or "-")
        active_prefix = "[ACTIVE] " if bool(payload.get("is_active")) else ""
        retry_prefix = "[RETRY] " if bool((payload.get("metadata") or {}).get("retry_mode")) else ""
        return (
            f"{active_prefix}{retry_prefix}{payload.get('target_fingerprint', '')}\n"
            f"{payload.get('target_provider', '')} / {payload.get('target_model', '')}\n"
            f"promoted_at={promoted_at}"
        )

    def _render_lineage(self) -> None:
        theme = resolve_theme(self)
        if not self._lineage:
            self.lineage_view.setHtml(
                f"<div style='font-weight:700; color:{theme.text_primary};'>Generation Lineage</div>"
                f"<div style='margin-top:6px; color:{theme.text_secondary};'>No promoted history yet. The current active generation will appear here once a job is promoted.</div>"
            )
            return
        blocks = [
            f"<div style='font-weight:700; color:{theme.text_primary};'>Active Generation Lineage</div>",
            f"<div style='margin-top:4px; color:{theme.text_secondary};'>Current active, previous generation, and retry ancestry. Keep it compact, keep it traceable.</div>",
        ]
        for item in self._lineage[:5]:
            role = str(item.get("generation_role", "history") or "history")
            role_tone = "active" if item.get("is_active") else ("warning" if role == "previous" else "neutral")
            role_label = "CURRENT" if item.get("is_active") else role.upper()
            retry_label = (
                f" - retry={html.escape(str(item.get('retry_mode', '') or ''))}"
                if str(item.get("retry_mode", "") or "").strip()
                else ""
            )
            parent_label = (
                f"<div style='color:{theme.text_secondary}; font-size:11px; margin-top:2px;'>parent={html.escape(str(item.get('parent_job_id', '') or '-'))}{retry_label}</div>"
                if str(item.get("parent_job_id", "") or "").strip() or str(item.get("retry_mode", "") or "").strip()
                else ""
            )
            blocks.append(
                f"<div style='margin-top:10px; padding:10px 12px; background:{theme.surface}; border:1px solid {theme.divider}; border-radius:14px;'>"
                f"{_status_badge(role_label, role_tone)}"
                f"<span style='color:{theme.text_primary}; font-weight:700;'>{html.escape(str(item.get('target_fingerprint', '') or '-'))}</span>"
                f"<div style='color:{theme.text_secondary}; margin-top:4px;'>{html.escape(str(item.get('provider', '') or '-'))} / {html.escape(str(item.get('model', '') or '-'))} / {html.escape(str(item.get('backend', '') or '-'))}</div>"
                f"<div style='color:{theme.text_secondary}; font-size:11px; margin-top:2px;'>promoted_at={html.escape(str(item.get('promoted_at', '') or '-'))}</div>"
                f"{parent_label}"
                "</div>"
            )
        self.lineage_view.setHtml("".join(blocks))

    def _render_current(self, list_name: str, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        theme = resolve_theme(self)
        if current is None:
            self.detail_title.setText("Job Details")
            self.detail_view.setHtml(f"<div style='color:{theme.text_secondary};'>Select a reindex job to inspect its lifecycle and health.</div>")
            self.raw_health_view.hide()
            self._update_buttons()
            return
        job_id = str(current.data(Qt.UserRole) or "")
        payload = self._jobs.get(job_id, {}) if list_name == "jobs" else self._promoted_jobs.get(job_id, {})
        self.detail_title.setText(job_id)
        self.detail_view.setHtml(self._build_detail_html(payload))
        summary = payload.get("health_check_summary", {}) if isinstance(payload.get("health_check_summary"), dict) else {}
        self.raw_health_view.setPlainText(json.dumps(summary, ensure_ascii=False, indent=2))
        self.raw_health_view.setVisible(self._show_raw_health)
        self._update_buttons()

    def _build_detail_html(self, payload: dict[str, Any]) -> str:
        theme = resolve_theme(self)
        job = ReindexJob.from_row(payload) if payload else None
        if job is None:
            return f"<div style='color:{theme.text_secondary};'>No job details available.</div>"

        overview = build_health_check_overview(job.health_check_summary)
        category_counts = payload.get("failed_chunk_category_counts", {}) if isinstance(payload.get("failed_chunk_category_counts"), dict) else {}
        health_html = "".join(
            "<li>"
            f"{_status_badge('PASS' if row['passed'] else 'FAIL', 'success' if row['passed'] else 'error')}"
            f"{html.escape(str(row['label']))}: {html.escape(str(row['value']))}"
            "</li>"
            for row in overview["rows"]
        )
        warnings = [str(item) for item in overview["warnings"] if str(item).strip()]
        reasons = [str(item) for item in overview["reasons"] if str(item).strip()]
        warnings_html = "".join(f"<li>{html.escape(item)}</li>" for item in warnings) or "<li>None</li>"
        reasons_html = "".join(f"<li>{html.escape(item)}</li>" for item in reasons) or "<li>None</li>"
        parent_html = ""
        if job.is_retry_job:
            parent_html = (
                f"<div style='margin-top:6px; color:{theme.text_secondary};'>"
                f"retry_source={html.escape(job.retry_source_job_id)} - "
                f"parent_job_id={html.escape(job.parent_job_id)} - "
                f"retry_mode={html.escape(job.retry_mode)} - "
                f"retry_reason_filter={html.escape(str(job.metadata.get('retry_reason_filter', '') or 'all'))}"
                "</div>"
            )
        active_html = (
            "<div style='margin-top:6px;'>"
            f"{_status_badge('ACTIVE', 'active')}This promoted generation currently backs retrieval."
            "</div>"
            if bool(payload.get("is_active"))
            else ""
        )
        promoted_html = (
            f"<div style='margin-top:6px;'>{_status_badge('PROMOTED', 'active')}promoted_at={html.escape(job.promoted_at or '-')}</div>"
            if job.promotion_status == "promoted"
            else ""
        )
        failure_breakdown = ""
        if category_counts:
            bits = [f"{html.escape(str(key))}={int(value or 0)}" for key, value in category_counts.items()]
            failure_breakdown = "<div style='margin-bottom:8px;'>failed categories=" + " - ".join(bits) + "</div>"
        overall_chip = _status_badge("HEALTHY", "success") if overview["overall_passed"] else _status_badge("NEEDS REVIEW", "warning")
        return (
            f"<div style='font-size:13px; color:{theme.text_primary}; font-weight:700; margin-bottom:8px;'>{html.escape(job.job_id)}</div>"
            f"<div style='color:{theme.text_secondary}; margin-bottom:10px;'>status={html.escape(job.status)} - promotion={html.escape(job.promotion_status)} - completion={html.escape(job.completion_reason or '-')}</div>"
            f"<div style='margin-bottom:8px;'>source={html.escape(job.source_fingerprint or '-')} -> target={html.escape(job.target_fingerprint or '-')}</div>"
            f"<div style='margin-bottom:8px;'>provider={html.escape(job.target_provider or '-')} - model={html.escape(job.target_model or '-')} - backend={html.escape(job.backend_type or '-')}</div>"
            f"<div style='margin-bottom:8px;'>collection={html.escape(job.output_collection_name or '-')} - dim={job.output_vector_dim}</div>"
            f"<div style='margin-bottom:8px;'>progress={job.processed_chunks}/{job.total_chunks} - succeeded={job.succeeded_chunks} - failed={job.failed_chunks} - skipped={job.skipped_chunks} - warnings={job.warning_count}</div>"
            f"{failure_breakdown}"
            f"<div style='margin-bottom:8px;'>created={html.escape(job.created_at or '-')} - started={html.escape(job.started_at or '-')} - finished={html.escape(job.finished_at or '-')}</div>"
            f"{parent_html}{promoted_html}{active_html}"
            f"<div style='margin-top:12px; color:{theme.text_primary}; font-weight:700;'>Health Check</div>"
            f"<div style='margin-top:6px;'>{overall_chip}"
            f"<span style='color:{theme.text_secondary};'>passed={overview['passed_count']} - failed={overview['failed_count']} - warnings={overview['warnings_count']} - reasons={overview['reasons_count']}</span></div>"
            f"<ul>{health_html}</ul>"
            f"<div style='margin-top:10px; color:{theme.text_primary}; font-weight:700;'>Warnings</div>"
            f"<ul>{warnings_html}</ul>"
            f"<div style='margin-top:10px; color:{theme.text_primary}; font-weight:700;'>Reasons</div>"
            f"<ul>{reasons_html}</ul>"
            f"<div style='margin-top:10px; color:{theme.error};'>last_error: {html.escape(job.last_error or '-')}</div>"
        )

    def _toggle_raw_health(self) -> None:
        self._show_raw_health = not self._show_raw_health
        self.raw_health_view.setVisible(self._show_raw_health)

    def _request_reindex(self) -> None:
        callback = self.on_reindex_requested
        if callback is not None:
            callback()

    def _emit_job_action(self, action: str) -> None:
        current = self.tabs.currentWidget()
        if not isinstance(current, QListWidget):
            return
        item = current.currentItem()
        if item is None:
            return
        callback = self.on_job_action
        if callback is not None:
            callback(action, str(item.data(Qt.UserRole) or ""))

    def _update_buttons(self) -> None:
        current_widget = self.tabs.currentWidget()
        current_item = current_widget.currentItem() if isinstance(current_widget, QListWidget) else None
        payload: dict[str, Any] = {}
        if current_item is not None:
            job_id = str(current_item.data(Qt.UserRole) or "")
            payload = self._jobs.get(job_id, {}) if current_widget is self.jobs_list else self._promoted_jobs.get(job_id, {})
        status = str(payload.get("status", "") or "")
        promotion = str(payload.get("promotion_status", "") or "")
        failed_chunks = int(payload.get("failed_chunks", 0) or 0)
        category_counts = payload.get("failed_chunk_category_counts", {}) if isinstance(payload.get("failed_chunk_category_counts"), dict) else {}
        self.cancel_button.setEnabled(current_widget is self.jobs_list and status in {"queued", "running", "cancel_requested"})
        self.promote_button.setEnabled(promotion == "eligible" and status in {"completed", "completed_with_errors"})
        self.retry_button.setEnabled(current_widget is self.jobs_list and status == "completed_with_errors" and failed_chunks > 0)
        self.retry_all_action.setEnabled(current_widget is self.jobs_list and status == "completed_with_errors" and failed_chunks > 0)
        self.retry_embedding_action.setEnabled(
            current_widget is self.jobs_list
            and status == "completed_with_errors"
            and int(category_counts.get("embedding_provider_error", 0) or 0) > 0
        )
        self.retry_backend_action.setEnabled(
            current_widget is self.jobs_list
            and status == "completed_with_errors"
            and int(category_counts.get("backend_error", 0) or 0) > 0
        )
        self.raw_toggle_button.setEnabled(bool(payload))
