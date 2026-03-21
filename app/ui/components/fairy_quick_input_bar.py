from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QPushButton, QSizePolicy, QWidget

from app.config import llm_config
from app.ui.desktop_pet import ChatInputLineEdit
from app.ui.i18n import LANG_ZH, normalize_ui_language, tr
from app.ui.theme import resolve_theme, rgba


class FairyQuickInputBar(QWidget):
    sendRequested = Signal(str, list)
    escapePressed = Signal()
    hoverChanged = Signal(bool)
    focusChanged = Signal(bool)
    preferredWidthChanged = Signal(int)
    attachmentCountChanged = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        theme = resolve_theme(self)
        self._language = LANG_ZH
        self._pending_attachments: list[str] = []
        self._preferred_width = 160

        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.frame = QFrame(self)
        self.frame.setObjectName("quickInputFrame")
        self.frame.setStyleSheet(
            f"""
            QFrame#quickInputFrame {{
                background: {rgba('#FFFFFF', 191) if theme.scheme == 'light' else rgba('#282A30', 191)};
                border: 1px solid {rgba(theme.divider, 220)};
                border-radius: 18px;
            }}
            QPushButton {{
                background: transparent;
                border: none;
                color: {theme.text_secondary};
                min-width: 20px;
                min-height: 20px;
                padding: 0px;
                font-size: 14px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                color: {theme.text_primary};
            }}
            QLineEdit {{
                background: transparent;
                border: none;
                color: {theme.text_primary};
                selection-background-color: {rgba(theme.fairy_blue_soft, 92)};
                padding: 0px 2px;
                font-size: 13px;
            }}
            """
        )

        layout = QHBoxLayout(self.frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        self.attach_button = QPushButton("+", self.frame)
        self.attach_button.setFocusPolicy(Qt.NoFocus)
        self.attach_button.clicked.connect(self._pick_attachments)

        self.input_edit = ChatInputLineEdit(self.frame)
        self.input_edit.setFrame(False)
        self.input_edit.textEdited.connect(self._emit_preferred_width)
        self.input_edit.returnPressed.connect(self._handle_submit)
        self.input_edit.installEventFilter(self)

        self.voice_button = QPushButton("o", self.frame)
        self.voice_button.setFocusPolicy(Qt.NoFocus)
        self.voice_button.clicked.connect(self._show_voice_placeholder)

        layout.addWidget(self.attach_button, 0)
        layout.addWidget(self.input_edit, 1)
        layout.addWidget(self.voice_button, 0)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.frame)

        self.frame.installEventFilter(self)
        self.installEventFilter(self)
        self.set_ui_language(self._language)
        self._emit_preferred_width()

    def set_ui_language(self, language: str) -> None:
        self._language = normalize_ui_language(language)
        self.input_edit.setPlaceholderText(tr("presence_input_placeholder", self._language))
        self.voice_button.setToolTip(tr("presence_voice_placeholder", self._language))
        self._refresh_attachment_state()

    def preferred_width(self) -> int:
        return self._preferred_width

    def attachment_names(self) -> list[str]:
        return [Path(item).name for item in self._pending_attachments]

    def has_focus_within(self) -> bool:
        return self.input_edit.hasFocus()

    def is_effectively_empty(self) -> bool:
        return not self.input_edit.text().strip() and not self._pending_attachments

    def clear_input(self) -> None:
        self.input_edit.clear()
        self._pending_attachments.clear()
        self._refresh_attachment_state()
        self._emit_preferred_width()

    def focus_input(self) -> None:
        self.input_edit.setFocus(Qt.MouseFocusReason)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.escapePressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.input_edit:
            if event.type() == QEvent.FocusIn:
                self.focusChanged.emit(True)
            elif event.type() == QEvent.FocusOut:
                self.focusChanged.emit(False)
            elif event.type() == QEvent.KeyPress:
                key_event = event
                if isinstance(key_event, QKeyEvent) and key_event.key() == Qt.Key_Escape:
                    self.escapePressed.emit()
                    return True
        if watched in {self, self.frame}:
            if event.type() == QEvent.Enter:
                self.hoverChanged.emit(True)
            elif event.type() == QEvent.Leave:
                self.hoverChanged.emit(False)
        return super().eventFilter(watched, event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.hoverChanged.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hoverChanged.emit(False)
        super().leaveEvent(event)

    def _handle_submit(self) -> None:
        if self.input_edit.is_composing:
            return
        text = self.input_edit.text().strip()
        attachments = list(self._pending_attachments)
        if not text and not attachments:
            return
        self.sendRequested.emit(text, attachments)
        self.clear_input()

    def _pick_attachments(self) -> None:
        filters = (
            "Supported Files (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.txt *.md *.json *.csv *.pdf *.docx *.xlsx);;"
            "All Files (*.*)"
        )
        files, _ = QFileDialog.getOpenFileNames(self, tr("presence_select_attachments", self._language), "", filters)
        if not files:
            return
        for path in files:
            if path not in self._pending_attachments:
                self._pending_attachments.append(path)
            if len(self._pending_attachments) >= llm_config.max_attachment_files:
                break
        self._refresh_attachment_state()
        self._emit_preferred_width()

    def _show_voice_placeholder(self) -> None:
        self.voice_button.setToolTip(tr("presence_voice_placeholder", self._language))

    def _refresh_attachment_state(self) -> None:
        has_attachments = bool(self._pending_attachments)
        self.attach_button.setText("*" if has_attachments else "+")
        if has_attachments:
            self.attach_button.setToolTip(
                tr("presence_attached_prefix", self._language) + ", ".join(self.attachment_names()[:3])
            )
        else:
            self.attach_button.setToolTip(tr("presence_attach", self._language))
        self.attachmentCountChanged.emit(len(self._pending_attachments))

    def _emit_preferred_width(self) -> None:
        metrics = self.input_edit.fontMetrics()
        text = self.input_edit.text()
        text_width = metrics.horizontalAdvance(text) + 24 if text else 0
        attachment_bonus = 18 if self._pending_attachments else 0
        button_width = 26 + 26 + 42
        preferred = max(160, min(300, text_width + button_width + attachment_bonus))
        if preferred != self._preferred_width:
            self._preferred_width = preferred
            self.preferredWidthChanged.emit(preferred)
