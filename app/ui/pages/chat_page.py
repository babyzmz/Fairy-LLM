from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import Property, QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config import llm_config
from app.ui.components.chat import ChatMessage, DisplayMode, MessageWidget, RendererRegistry, normalize_display_mode
from app.ui.components.status_chip import StatusChip
from app.ui.i18n import LANG_ZH, localize_mode_label, localize_route_label, normalize_ui_language, tr
from app.ui.theme import apply_soft_shadow, button_style, card_style, line_edit_style, mix, resolve_theme, rgba


class _MessageRow(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._message_widget: MessageWidget | None = None
        self._reveal_offset = 0.0
        self._base_margins = (4, 0, 4, 0)
        self._last_size_hint = QSize(240, 0)
        self.row_layout = QHBoxLayout(self)
        self.row_layout.setContentsMargins(*self._base_margins)
        self.row_layout.setSpacing(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_message_widget(self, widget: MessageWidget) -> None:
        self._message_widget = widget
        self.updateGeometry()

    def message_widget(self) -> MessageWidget | None:
        return self._message_widget

    def get_reveal_offset(self) -> float:
        return self._reveal_offset

    def set_reveal_offset(self, value: float) -> None:
        self._reveal_offset = max(0.0, float(value))
        left, _top, right, bottom = self._base_margins
        self.row_layout.setContentsMargins(left, round(self._reveal_offset), right, bottom)
        self.updateGeometry()

    revealOffset = Property(float, get_reveal_offset, set_reveal_offset)

    def sync_to_width(self, width: int) -> None:
        margins = self.row_layout.contentsMargins()
        content_width = max(120, width - margins.left() - margins.right())
        if self._message_widget is None:
            total_height = margins.top() + margins.bottom()
            self.setMinimumHeight(total_height)
            self.setMaximumHeight(total_height)
            return
        widget_width = min(content_width, self._message_widget.content_max_width())
        child_height = self._message_widget.preferred_height_for_width(widget_width)
        self._message_widget.setFixedWidth(widget_width)
        self._message_widget.setMinimumHeight(child_height)
        self._message_widget.setMaximumHeight(child_height)
        total_height = margins.top() + child_height + margins.bottom()
        self.setMinimumHeight(total_height)
        self.setMaximumHeight(total_height)
        self._last_size_hint = QSize(max(240, width), total_height)
        self.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._last_size_hint)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.updateGeometry()


class ChatPage(QWidget):
    def __init__(self, parent=None, *, display_mode: DisplayMode = "normal") -> None:
        super().__init__(parent)
        theme = resolve_theme(self)
        self._display_mode = normalize_display_mode(display_mode)
        self._language = LANG_ZH
        self.on_send: Callable[[str, list[str]], None] | None = None
        self._pending_attachments: list[str] = []
        self._current_task_text = ""
        self._messages: list[ChatMessage] = []
        self._message_widgets: dict[str, MessageWidget] = {}
        self._message_rows: dict[str, _MessageRow] = {}
        self._message_animations: list[QParallelAnimationGroup] = []
        self._autoscroll_timers: list[QTimer] = []
        self._renderer_registry = RendererRegistry()
        self._demo_injected = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self.summary_card = QFrame(self)
        self.summary_card.setStyleSheet(
            f"""
            {card_style(theme, 'QFrame', radius=18, soft=True)}
            QLabel {{
                color: {theme.text_primary};
            }}
            """
        )
        apply_soft_shadow(self.summary_card, theme, blur=20, y_offset=8, strength=0.8)
        summary_layout = QVBoxLayout(self.summary_card)
        summary_layout.setContentsMargins(14, 14, 14, 14)
        summary_layout.setSpacing(8)
        self.summary_title = QLabel("", self.summary_card)
        self.summary_title.setStyleSheet(f"font-size:16px; font-weight:800; color:{theme.text_primary};")
        self.system_summary = QLabel("", self.summary_card)
        self.system_summary.setWordWrap(True)
        self.system_summary.setStyleSheet(f"font-size:12px; color:{theme.text_secondary};")
        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        self.mode_chip = StatusChip("normal", "active", self.summary_card)
        self.route_chip = StatusChip("local", "inactive", self.summary_card)
        self.knowledge_chip = StatusChip("0 pending", "inactive", self.summary_card)
        chip_row.addWidget(self.mode_chip)
        chip_row.addWidget(self.route_chip)
        chip_row.addWidget(self.knowledge_chip)
        chip_row.addStretch(1)
        summary_layout.addWidget(self.summary_title)
        summary_layout.addWidget(self.system_summary)
        summary_layout.addLayout(chip_row)
        root.addWidget(self.summary_card)

        self.task_label = QLabel("", self)
        self.task_label.setStyleSheet(
            f"font-size:12px; color:{theme.text_secondary}; font-weight:700; letter-spacing:0.3px;"
        )
        root.addWidget(self.task_label)

        self.chat_surface = QFrame(self)
        self.chat_surface.setStyleSheet(
            f"""
            {card_style(theme, 'QFrame', radius=24, soft=False)}
            QScrollArea {{
                border: none;
                background: transparent;
            }}
            QWidget#messageColumn {{
                background: transparent;
            }}
            """
        )
        apply_soft_shadow(self.chat_surface, theme, blur=24, y_offset=10, strength=0.95)
        chat_surface_layout = QVBoxLayout(self.chat_surface)
        chat_surface_layout.setContentsMargins(12, 12, 12, 12)
        chat_surface_layout.setSpacing(0)

        self.chat_scroll = QScrollArea(self.chat_surface)
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QScrollArea.NoFrame)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chat_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.chat_scroll.setStyleSheet(
            f"background:{rgba(mix(theme.surface, theme.fairy_blue_deep, 0.03), 252 if theme.scheme == 'light' else 244)};"
        )

        self.message_column = QWidget(self.chat_scroll)
        self.message_column.setObjectName("messageColumn")
        self.message_column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.message_layout = QVBoxLayout(self.message_column)
        self.message_layout.setContentsMargins(6, 10, 6, 10)
        self.message_layout.setSpacing(18)
        self.message_layout.addStretch(1)
        self.chat_scroll.setWidget(self.message_column)
        chat_surface_layout.addWidget(self.chat_scroll)
        root.addWidget(self.chat_surface, 1)

        self.attachment_label = QLabel("", self)
        self.attachment_label.setStyleSheet(f"font-size:11px; color:{theme.text_secondary};")
        root.addWidget(self.attachment_label)

        input_wrap = QFrame(self)
        input_wrap.setStyleSheet(
            f"""
            {card_style(theme, 'QFrame', radius=20, soft=False)}
            {button_style(theme)}
            {line_edit_style(theme, radius=14)}
            """
        )
        apply_soft_shadow(input_wrap, theme, blur=20, y_offset=8, strength=0.8)
        input_layout = QHBoxLayout(input_wrap)
        input_layout.setContentsMargins(12, 12, 12, 12)
        input_layout.setSpacing(8)
        self.attach_button = QPushButton(input_wrap)
        self.clear_button = QPushButton(input_wrap)
        self.input_edit = QLineEdit(input_wrap)
        self.input_edit.returnPressed.connect(self._handle_send)
        self.send_button = QPushButton(input_wrap)
        self.send_button.setStyleSheet(button_style(theme, tone="accent"))
        self.attach_button.clicked.connect(self._pick_attachments)
        self.clear_button.clicked.connect(self._clear_input)
        self.send_button.clicked.connect(self._handle_send)
        input_layout.addWidget(self.attach_button)
        input_layout.addWidget(self.clear_button)
        input_layout.addWidget(self.input_edit, 1)
        input_layout.addWidget(self.send_button)
        root.addWidget(input_wrap)

        self.set_ui_language(self._language)
        if os.getenv("FAIRY_CHAT_UI_DEMO", "").strip() == "1":
            QTimer.singleShot(0, self.inject_demo_messages)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_message_layout()

    def set_ui_language(self, language: str) -> None:
        self._language = normalize_ui_language(language)
        self.summary_title.setText(tr("chat_session_title", self._language))
        self.system_summary.setText(tr("chat_default_summary", self._language))
        self.attach_button.setText(tr("chat_attach", self._language))
        self.clear_button.setText(tr("chat_clear", self._language))
        self.send_button.setText(tr("chat_send", self._language))
        self.input_edit.setPlaceholderText(tr("chat_input_placeholder", self._language))
        self.set_current_task(self._current_task_text)
        self._refresh_attachment_label()
        if self._messages:
            self._rerender_messages()

    def add_message(self, speaker: str, text: str, *, rich_text: bool = False) -> str:
        return self.push_legacy_message(speaker, text, rich_text=rich_text)

    def push_legacy_message(self, speaker: str, text: str, *, rich_text: bool = False) -> str:
        message = ChatMessage.from_legacy(speaker, text, rich_text=rich_text)
        self.push_message(message)
        return message.id

    def add_chat_message(self, message: ChatMessage) -> None:
        self.push_message(message)

    def push_message(self, message: ChatMessage) -> None:
        self._messages.append(message)
        self._render_message(message, animate=True)

    def update_message(self, message_id: str, *, text: str | None = None, payload: dict | None = None) -> None:
        for message in self._messages:
            if message.id != message_id:
                continue
            if payload:
                message.payload.update(payload)
            if text is not None:
                message.payload["text"] = text
            widget = self._message_widgets.get(message_id)
            if widget is not None:
                widget.update_message(message)
                widget.updateGeometry()
            row = self._message_rows.get(message_id)
            if row is not None:
                row.updateGeometry()
            self._refresh_message_layout()
            self._scroll_to_bottom()
            break

    def set_current_task(self, text: str) -> None:
        self._current_task_text = text
        display = text.strip() or tr("chat_waiting", self._language)
        self.task_label.setText(f"{tr('chat_task_prefix', self._language)} - {display}")

    def set_runtime_summary(
        self,
        *,
        mode_label: str,
        route_label: str,
        pending_decisions: int,
        summary: str,
    ) -> None:
        self.mode_chip.set_chip(
            localize_mode_label(mode_label or "NORMAL", self._language),
            "active" if "GAME" in mode_label else "info",
        )
        self.route_chip.set_chip(
            localize_route_label(route_label or "local", self._language),
            "running" if "cloud" in route_label else "inactive",
        )
        self.knowledge_chip.set_chip(
            tr("status_pending", self._language, count=pending_decisions),
            "pending" if pending_decisions else "inactive",
        )
        self.system_summary.setText(summary or tr("chat_online", self._language))

    def clear_trace(self) -> None:
        for timer in list(self._autoscroll_timers):
            timer.stop()
            timer.deleteLater()
        self._messages.clear()
        self._message_widgets.clear()
        self._message_rows.clear()
        self._message_animations.clear()
        self._autoscroll_timers.clear()
        while self.message_layout.count() > 1:
            item = self.message_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._refresh_message_layout()

    def inject_demo_messages(self) -> None:
        if self._demo_injected:
            return
        self._demo_injected = True
        self.clear_trace()
        demo_messages = [
            ChatMessage.from_legacy("You", "今天悉尼天气怎么样？"),
            ChatMessage.create(
                role="assistant",
                message_type="weather",
                payload={
                    "city": "Sydney",
                    "temp": 24,
                    "high": 27,
                    "low": 18,
                    "feels_like": 25,
                    "wind": "18 km/h",
                    "condition": "Partly Cloudy",
                    "hourly_curve": [18, 19, 21, 23, 24, 25, 24, 22],
                },
            ),
            ChatMessage.create(
                role="assistant",
                message_type="suggestion",
                payload={
                    "body": "下午体感会比温度略高，外出更适合轻薄衣物。如果你要出门通勤，我建议把晚上回落温度也一起考虑进去。",
                },
            ),
            ChatMessage.create(
                role="assistant",
                message_type="news",
                payload={
                    "items": [
                        {
                            "title": "OpenAI 发布新的开发者能力更新",
                            "summary": "这次更新更偏向模型调用稳定性与工具链集成，适合本地 AI 助手和工作流产品接入。",
                            "tags": ["AI", "API", "Product"],
                            "url": "https://openai.com",
                        },
                        {
                            "title": "Qt Widgets 仍在桌面工具场景保持高效率",
                            "summary": "在需要高密度交互和长期运行的本地应用里，Widgets 仍然有很强的实用价值。",
                            "tags": ["Qt", "Desktop"],
                            "url": "https://www.qt.io",
                        },
                    ],
                },
            ),
            ChatMessage.create(
                role="assistant",
                message_type="link",
                payload={
                    "title": "Fairy 文档入口",
                    "url": "https://platform.openai.com/docs",
                },
            ),
            ChatMessage.create(
                role="assistant",
                message_type="map",
                payload={
                    "address": "Circular Quay, Sydney NSW",
                    "distance": "距你约 3.2 km",
                    "url": "https://maps.google.com/?q=Circular+Quay+Sydney",
                },
            ),
        ]
        for message in demo_messages:
            self.push_message(message)

    def _render_message(self, message: ChatMessage, *, animate: bool) -> None:
        row = _MessageRow(self.message_column)
        widget = self._renderer_registry.create_widget(
            message,
            row,
            language=self._language,
            display_mode=self._display_mode,
        )
        row.set_message_widget(widget)

        if message.role == "user":
            row.row_layout.addStretch(1)
            row.row_layout.addWidget(widget, 0, Qt.AlignRight | Qt.AlignTop)
        elif message.role == "system":
            row.row_layout.addStretch(1)
            row.row_layout.addWidget(widget, 0, Qt.AlignHCenter | Qt.AlignTop)
            row.row_layout.addStretch(1)
        else:
            row.row_layout.addWidget(widget, 0, Qt.AlignLeft | Qt.AlignTop)
            row.row_layout.addStretch(1)

        insert_at = max(0, self.message_layout.count() - 1)
        self.message_layout.insertWidget(insert_at, row)
        self._message_widgets[message.id] = widget
        self._message_rows[message.id] = row
        self._refresh_message_layout()
        self._scroll_to_bottom(row)
        if animate:
            QTimer.singleShot(0, lambda row=row: self._animate_row(row))

    def _rerender_messages(self) -> None:
        messages = list(self._messages)
        self.clear_trace()
        for message in messages:
            self._messages.append(message)
            self._render_message(message, animate=False)
        self._refresh_message_layout()

    def _animate_row(self, row: _MessageRow) -> None:
        opacity = QGraphicsOpacityEffect(row)
        row.setGraphicsEffect(opacity)
        opacity.setOpacity(0.18)

        fade = QPropertyAnimation(opacity, b"opacity", row)
        fade.setDuration(180)
        fade.setStartValue(0.18)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)

        row.set_reveal_offset(14.0)
        slide = QPropertyAnimation(row, b"revealOffset", row)
        slide.setDuration(180)
        slide.setStartValue(14.0)
        slide.setEndValue(0.0)
        slide.setEasingCurve(QEasingCurve.OutCubic)

        group = QParallelAnimationGroup(row)
        group.addAnimation(fade)
        group.addAnimation(slide)
        group.finished.connect(lambda grp=group, target=row, fx=opacity: self._finish_row_animation(grp, target, fx))
        self._message_animations.append(group)
        group.start()

    def _finish_row_animation(self, group: QParallelAnimationGroup, row: _MessageRow, opacity: QGraphicsOpacityEffect) -> None:
        opacity.setOpacity(1.0)
        row.setGraphicsEffect(None)
        if group in self._message_animations:
            self._message_animations.remove(group)
        self._scroll_to_bottom(row)

    def _refresh_message_layout(self) -> None:
        available_width = max(240, self.chat_scroll.viewport().width() - 12)
        for row in self._message_rows.values():
            row.sync_to_width(available_width)
        self.message_layout.invalidate()
        self.message_column.updateGeometry()
        self.message_column.adjustSize()
        self.chat_scroll.widget().updateGeometry()
        self.chat_scroll.viewport().update()

    def _scroll_to_bottom(self, row: _MessageRow | None = None) -> None:
        bar = self.chat_scroll.verticalScrollBar()
        delays = (0, 40, 120, 260)

        def apply_scroll() -> None:
            if row is not None:
                self.chat_scroll.ensureWidgetVisible(row, 0, 28)
            bar.setValue(bar.maximum())

        for delay in delays:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(apply_scroll)
            timer.timeout.connect(lambda t=timer: self._autoscroll_timers.remove(t) if t in self._autoscroll_timers else None)
            self._autoscroll_timers.append(timer)
            timer.start(delay)

    def _handle_send(self) -> None:
        text = self.input_edit.text().strip()
        attachments = list(self._pending_attachments)
        if not text and not attachments:
            return
        self.input_edit.clear()
        callback = self.on_send
        if callback is not None:
            callback(text, attachments)
        self._pending_attachments.clear()
        self._refresh_attachment_label()

    def _clear_input(self) -> None:
        self.input_edit.clear()
        self._pending_attachments.clear()
        self._refresh_attachment_label()

    def _pick_attachments(self) -> None:
        filters = (
            "Supported Files (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.txt *.md *.json *.csv *.pdf *.docx *.xlsx);;"
            "All Files (*.*)"
        )
        files, _ = QFileDialog.getOpenFileNames(self, tr("chat_select_files", self._language), "", filters)
        if not files:
            return
        for path in files:
            if path not in self._pending_attachments:
                self._pending_attachments.append(path)
            if len(self._pending_attachments) >= llm_config.max_attachment_files:
                break
        self._refresh_attachment_label()

    def _refresh_attachment_label(self) -> None:
        if not self._pending_attachments:
            self.attachment_label.setText("")
            return
        names = [path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for path in self._pending_attachments]
        label = ", ".join(names[:2])
        if len(names) > 2:
            label += f" +{len(names) - 2}"
        self.attachment_label.setText(f"{tr('chat_attached_prefix', self._language)} - {label}")
