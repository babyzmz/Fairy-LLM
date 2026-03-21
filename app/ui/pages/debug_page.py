from __future__ import annotations

import html

from PySide6.QtWidgets import QFrame, QLabel, QTextBrowser, QVBoxLayout, QWidget

from app.models.action_event import ActionEvent
from app.ui.retrieval_debug_panel import RetrievalDebugPanel
from app.ui.theme import apply_soft_shadow, card_style, resolve_theme, text_browser_style


class DebugPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        theme = resolve_theme(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self.retrieval_panel = RetrievalDebugPanel(self)
        root.addWidget(self.retrieval_panel)

        self.timeline = QTextBrowser(self)
        self.timeline.setStyleSheet(text_browser_style(theme, radius=18))
        apply_soft_shadow(self.timeline, theme, blur=18, y_offset=8, strength=0.8)
        self.timeline.setOpenExternalLinks(True)
        self.timeline.document().setDocumentMargin(0)
        root.addWidget(self._wrap("Action Timeline", self.timeline), 1)

        self.terminal = QTextBrowser(self)
        self.terminal.setStyleSheet(text_browser_style(theme, radius=18))
        apply_soft_shadow(self.terminal, theme, blur=18, y_offset=8, strength=0.8)
        self.terminal.document().setDocumentMargin(0)
        root.addWidget(self._wrap("Terminal Output", self.terminal), 1)

        self.artifacts = QTextBrowser(self)
        self.artifacts.setStyleSheet(text_browser_style(theme, radius=18))
        apply_soft_shadow(self.artifacts, theme, blur=18, y_offset=8, strength=0.8)
        self.artifacts.document().setDocumentMargin(0)
        root.addWidget(self._wrap("Session Artifacts", self.artifacts), 1)

    def _wrap(self, title: str, inner: QWidget) -> QWidget:
        theme = resolve_theme(self)
        frame = QFrame(self)
        frame.setStyleSheet(f"{card_style(theme, 'QFrame', radius=18, soft=False)} QLabel {{ color:{theme.text_primary}; font-size:15px; font-weight:800; }}")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        label = QLabel(title, frame)
        layout.addWidget(label)
        layout.addWidget(inner, 1)
        return frame

    def append_action_event(self, event: ActionEvent) -> None:
        theme = resolve_theme(self)
        status_color = {
            "info": theme.fairy_blue_core,
            "running": theme.warning,
            "done": theme.success,
            "failed": theme.error,
        }.get(event.status, theme.text_secondary)
        detail = html.escape(event.detail).replace("\n", "<br>")
        self.timeline.append(
            "<div style='margin-bottom:8px;'>"
            f"<span style='color:{status_color}; font-weight:700;'>[{html.escape(event.status)}]</span> "
            f"<span style='color:{theme.text_primary}; font-weight:700;'>{html.escape(event.title)}</span>"
            f"<div style='margin-left:12px; color:{theme.text_secondary};'>{detail}</div>"
            "</div>"
        )
        self.timeline.verticalScrollBar().setValue(self.timeline.verticalScrollBar().maximum())

    def append_terminal_output(self, stream_name: str, text: str) -> None:
        theme = resolve_theme(self)
        tone = theme.fairy_blue_core if stream_name == "stdout" else theme.error
        self.terminal.append(
            f"<div style='margin-bottom:6px; color:{tone};'><b>{html.escape(stream_name)}</b> {html.escape(text).replace(chr(10), '<br>')}</div>"
        )
        self.terminal.verticalScrollBar().setValue(self.terminal.verticalScrollBar().maximum())

    def show_session_artifacts(self, changed_files: list[str], commands_run: list[str], validations: list[dict[str, object]]) -> None:
        theme = resolve_theme(self)
        lines: list[str] = []
        if changed_files:
            lines.append(f"<div style='color:{theme.text_primary}; font-weight:700; margin-bottom:4px;'>Changed Files</div>")
            lines.extend(f"<li>{html.escape(path)}</li>" for path in changed_files[:12])
        if commands_run:
            lines.append(f"<div style='color:{theme.text_primary}; font-weight:700; margin:10px 0 4px;'>Commands</div>")
            lines.extend(f"<li>{html.escape(cmd)}</li>" for cmd in commands_run[:10])
        if validations:
            lines.append(f"<div style='color:{theme.text_primary}; font-weight:700; margin:10px 0 4px;'>Validations</div>")
            for item in validations[:10]:
                label = f"{item.get('name', '')}: {item.get('status', '')}"
                lines.append(f"<li>{html.escape(label)}</li>")
        if not lines:
            lines.append(f"<div style='color:{theme.text_secondary};'>No artifacts captured yet.</div>")
        self.artifacts.setHtml("<ul>" + "".join(lines) + "</ul>")

    def set_retrieval_snapshot(self, snapshot: dict[str, object] | None) -> None:
        if snapshot:
            self.retrieval_panel.set_snapshot(snapshot)
        else:
            self.retrieval_panel.clear_snapshot()

    def clear(self) -> None:
        self.timeline.clear()
        self.terminal.clear()
        self.artifacts.clear()
        self.retrieval_panel.clear_snapshot()
