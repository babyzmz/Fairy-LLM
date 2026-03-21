from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QEvent, QEasingCurve, Property, QPoint, QPropertyAnimation, QRect, Qt, QTimer
from PySide6.QtGui import QAction, QCursor, QGuiApplication
from PySide6.QtWidgets import QMenu, QPushButton, QVBoxLayout, QWidget

from app.ui.components.chat import ChatMessage
from app.ui.components.fairy_avatar_widget import FairyAvatarWidget
from app.ui.components.fairy_presence_reply_bubble import FairyPresenceReplyBubble
from app.ui.components.fairy_quick_input_bar import FairyQuickInputBar
from app.ui.desktop_pet import AvatarRenderer, FairyAvatar
from app.ui.i18n import LANG_ZH, normalize_ui_language, tr
from app.ui.theme import button_style, resolve_theme

logger = logging.getLogger(__name__)


class FairyPresenceWindow(QWidget):
    def __init__(
        self,
        avatar: AvatarRenderer | None = None,
        *,
        avatar_size: int = 80,
        quiet_mode: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        theme = resolve_theme(self)
        self._language = LANG_ZH
        self.on_send: Callable[[str, list[str]], None] | None = None
        self.on_open_console: Callable[[], None] | None = None
        self.on_open_page: Callable[[str], None] | None = None
        self.on_exit_requested: Callable[[], None] | None = None
        self.on_position_changed: Callable[[QPoint], None] | None = None
        self.on_quiet_mode_changed: Callable[[bool], None] | None = None
        self.on_ignore_notifications: Callable[[], None] | None = None

        self._avatar_size = max(64, min(112, int(avatar_size)))
        self._gap = 8
        self._margin = 6
        self._input_reveal = 0
        self._expanded = False
        self._quiet_mode = bool(quiet_mode)
        self._status = "idle"
        self._status_title = tr("presence_tooltip_idle_title", self._language)
        self._status_summary = tr("presence_tooltip_idle_summary", self._language)
        self._badge_count = 0
        self._avatar_hovered = False
        self._bar_hovered = False
        self._reply_hovered = False
        self._drag_origin_global: QPoint | None = None
        self._drag_origin_pos: QPoint | None = None
        self._drag_started = False
        self._ignorable_count = 0
        self._reply_visible = False
        self._last_valid_size = QRect(0, 0, 100, 100)  # Track last valid geometry for fallback

        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)

        self.avatar_widget = FairyAvatarWidget(avatar or FairyAvatar(), size=self._avatar_size, parent=self)
        self.avatar_widget.set_quiet_mode(self._quiet_mode)

        self.ignore_button = QPushButton(self)
        self.ignore_button.setObjectName("presenceIgnoreButton")
        self.ignore_button.setFocusPolicy(Qt.NoFocus)
        self.ignore_button.setCursor(Qt.PointingHandCursor)
        self.ignore_button.setStyleSheet(button_style(theme, compact=True))
        self.ignore_button.clicked.connect(self._ignore_notifications)
        self.ignore_button.hide()

        self.quick_input_bar = FairyQuickInputBar(self)
        self.quick_input_bar.hide()
        self.quick_input_bar.setMaximumHeight(0)

        self.reply_bubble = FairyPresenceReplyBubble(self)
        self.reply_bubble.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(self._margin, self._margin, self._margin, self._margin)
        layout.setSpacing(self._gap)
        layout.addWidget(self.avatar_widget, 0, Qt.AlignRight)
        layout.addWidget(self.ignore_button, 0, Qt.AlignRight)
        layout.addWidget(self.quick_input_bar, 0, Qt.AlignRight)
        layout.addWidget(self.reply_bubble, 0, Qt.AlignRight)

        self._input_target_height = max(self.quick_input_bar.sizeHint().height(), 42)

        self._expand_animation = QPropertyAnimation(self, b"inputReveal", self)
        self._expand_animation.setDuration(220)
        self._expand_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._expand_animation.finished.connect(self._handle_expand_finished)

        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._collapse_if_idle)

        self._reply_timer = QTimer(self)
        self._reply_timer.setSingleShot(True)
        self._reply_timer.timeout.connect(lambda: self.hide_reply_bubble(manual=False))

        self.avatar_widget.hoverChanged.connect(self._on_avatar_hover)
        self.avatar_widget.clicked.connect(self._focus_input)
        self.avatar_widget.doubleClicked.connect(self._open_console)
        self.avatar_widget.contextMenuRequested.connect(self._show_context_menu)

        self.quick_input_bar.hoverChanged.connect(self._on_bar_hover)
        self.quick_input_bar.focusChanged.connect(self._on_input_focus_changed)
        self.quick_input_bar.preferredWidthChanged.connect(self._on_preferred_width_changed)
        self.quick_input_bar.sendRequested.connect(self._handle_send)
        self.quick_input_bar.escapePressed.connect(self._handle_escape)

        self.reply_bubble.hoverChanged.connect(self._on_reply_hover_changed)
        self.reply_bubble.closeRequested.connect(lambda: self.hide_reply_bubble(manual=True))

        self.installEventFilter(self)
        self.avatar_widget.installEventFilter(self)
        self.set_ui_language(self._language)
        self._sync_geometry(anchor_right=False)

    def set_ui_language(self, language: str) -> None:
        self._language = normalize_ui_language(language)
        self.quick_input_bar.set_ui_language(self._language)
        self.reply_bubble.set_ui_language(self._language)
        self.set_ignore_action(self._ignorable_count > 0, count=self._ignorable_count)
        self.avatar_widget.set_presence_state(
            self._status,
            badge_count=self._badge_count,
            quiet_mode=self._quiet_mode,
            status_title=self._status_title,
            status_summary=self._status_summary,
        )

    def prepare_for_shutdown(self) -> None:
        self._collapse_timer.stop()
        self._reply_timer.stop()
        self._expand_animation.stop()
        self.on_send = None
        self.on_open_console = None
        self.on_open_page = None
        self.on_exit_requested = None
        self.on_position_changed = None
        self.on_quiet_mode_changed = None
        self.on_ignore_notifications = None

    def set_presence_state(self, status: str, *, badge_count: int = 0, summary: str = "", title: str = "") -> None:
        self._status = status
        self._badge_count = max(0, int(badge_count or 0))
        self._status_title = title.strip() or self._status_title
        self._status_summary = summary.strip() or self._status_summary
        self.avatar_widget.set_presence_state(
            status,
            badge_count=self._badge_count,
            quiet_mode=self._quiet_mode,
            status_title=self._status_title,
            status_summary=self._status_summary,
        )

    def set_quiet_mode(self, enabled: bool) -> None:
        self._quiet_mode = bool(enabled)
        self.avatar_widget.set_quiet_mode(self._quiet_mode)
        callback = self.on_quiet_mode_changed
        if callback is not None:
            callback(self._quiet_mode)

    def set_ignore_action(self, enabled: bool, *, count: int = 0) -> None:
        self._ignorable_count = max(0, int(count or 0)) if enabled else 0
        self.ignore_button.setVisible(self._ignorable_count > 0)
        if self._ignorable_count <= 0:
            self._sync_geometry(anchor_right=True)
            return
        label = tr("presence_ignore", self._language)
        if self._ignorable_count > 1:
            label = tr("presence_ignore_many", self._language, count=self._ignorable_count)
        self.ignore_button.setText(label)
        self.ignore_button.setToolTip(tr("presence_ignore_tooltip", self._language))
        self._sync_geometry(anchor_right=True)

    def show_reply_message(self, message: ChatMessage, *, error: bool = False) -> None:  # noqa: ARG002
        preview = message.text_preview().strip()
        if not preview:
            return
        self._reply_visible = True
        self.reply_bubble.set_message(message)
        self._sync_geometry(anchor_right=True)
        self._reply_timer.start(5200 if not error else 7000)

    def hide_reply_bubble(self, *, manual: bool) -> None:  # noqa: ARG002
        if not self._reply_visible:
            return
        self._reply_visible = False
        self._reply_timer.stop()
        self.reply_bubble.clear_reply()
        self._sync_geometry(anchor_right=True)

    def move_to_saved_or_default(self, point: QPoint | None = None) -> None:
        if point is None:
            geometry = self._available_geometry()
            x = geometry.right() - self.width() - 24
            y = geometry.bottom() - self.height() - 80
            self.move(x, y)
            return
        self.move(self._clamp_top_left(point))

    def inputReveal(self) -> int:  # noqa: N802
        return self._input_reveal

    def setInputReveal(self, value: int) -> None:  # noqa: N802
        height = max(0, min(self._input_target_height, int(value)))
        if height == self._input_reveal:
            return
        right_edge = self.frameGeometry().right()
        self._input_reveal = height
        self.quick_input_bar.setVisible(height > 0)
        self.quick_input_bar.setFixedWidth(self.quick_input_bar.preferred_width())
        self.quick_input_bar.setMaximumHeight(height)
        self.quick_input_bar.setMinimumHeight(0)
        self._sync_geometry(anchor_right=True, right_edge=right_edge)

    inputReveal = Property(int, inputReveal, setInputReveal)

    def paintEvent(self, event) -> None:  # noqa: N802
        """Handle paint event with defensive dirty rect validation for Windows layered windows.

        This prevents UpdateLayeredWindowIndirect errors on Windows when the card widget
        is displayed with invalid dirty rect parameters.
        """
        try:
            # Validate window geometry before painting
            self._validate_and_clamp_geometry()
            super().paintEvent(event)
        except Exception as e:
            logger.exception("paintEvent failed, attempting fallback: %s", e)
            # Fallback: force full window repaint
            try:
                self.update()
            except Exception as fallback_error:
                logger.exception("paintEvent fallback failed: %s", fallback_error)

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Handle resize event with geometry validation."""
        try:
            self._validate_and_clamp_geometry()
            super().resizeEvent(event)
            # Track valid size for fallback
            if event.size().width() > 0 and event.size().height() > 0:
                self._last_valid_size = QRect(0, 0, event.size().width(), event.size().height())
        except Exception as e:
            logger.exception("resizeEvent failed: %s", e)

    def _validate_and_clamp_geometry(self) -> None:
        """Validate and clamp window geometry to prevent UpdateLayeredWindowIndirect errors.

        Windows layered window updates require:
        - Window size > 0
        - Dirty rect coordinates >= 0
        - Dirty rect within window bounds
        - No negative offsets or oversized rects
        """
        try:
            rect = self.rect()
            width = rect.width()
            height = rect.height()

            # Ensure minimum valid size
            if width <= 0 or height <= 0:
                logger.warning(
                    "invalid_window_size width=%d height=%d, using fallback",
                    width,
                    height,
                )
                # Use last valid size or minimum
                fallback_width = max(100, self._last_valid_size.width())
                fallback_height = max(100, self._last_valid_size.height())
                self.setMinimumSize(fallback_width, fallback_height)
                self.resize(fallback_width, fallback_height)
                return

            # Validate all child widgets are within bounds
            for child in self.findChildren(QWidget):
                child_rect = child.geometry()
                if child_rect.right() > width or child_rect.bottom() > height:
                    logger.debug(
                        "child_widget_exceeds_bounds widget=%s rect=(%d,%d,%d,%d) window=(%d,%d)",
                        child.objectName(),
                        child_rect.left(),
                        child_rect.top(),
                        child_rect.right(),
                        child_rect.bottom(),
                        width,
                        height,
                    )
                    # Clamp child geometry
                    clamped = child_rect.intersected(rect)
                    if clamped.isValid():
                        child.setGeometry(clamped)

            # Log geometry for debugging DPI scaling issues
            dpi_ratio = self.devicePixelRatio()
            if dpi_ratio != 1.0:
                logger.debug(
                    "window_geometry_with_dpi window_size=(%d,%d) dpi_ratio=%.2f logical_size=(%d,%d)",
                    width,
                    height,
                    dpi_ratio,
                    int(width / dpi_ratio),
                    int(height / dpi_ratio),
                )

        except Exception as e:
            logger.exception("geometry_validation_failed: %s", e)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self._show_context_menu(event.globalPos())

    def eventFilter(self, watched, event) -> bool:
        if watched in {self, self.avatar_widget}:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag_origin_global = event.globalPosition().toPoint()
                self._drag_origin_pos = self.pos()
                self._drag_started = False
            elif event.type() == QEvent.MouseMove and self._drag_origin_global is not None and self._drag_origin_pos is not None:
                delta = event.globalPosition().toPoint() - self._drag_origin_global
                if not self._drag_started and delta.manhattanLength() < 5:
                    return False
                self._drag_started = True
                self.move(self._clamp_top_left(self._drag_origin_pos + delta))
                return True
            elif event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton and self._drag_origin_global is not None:
                if self._drag_started:
                    self._snap_to_edges()
                    self._save_position()
                    self._drag_origin_global = None
                    self._drag_origin_pos = None
                    self._drag_started = False
                    return True
                self._drag_origin_global = None
                self._drag_origin_pos = None
                self._drag_started = False
        return super().eventFilter(watched, event)

    def _on_avatar_hover(self, hovered: bool) -> None:
        self._avatar_hovered = hovered
        if hovered:
            self.expand_input(animated=True)
        else:
            self._schedule_collapse_check()

    def _on_bar_hover(self, hovered: bool) -> None:
        self._bar_hovered = hovered
        if hovered:
            self.expand_input(animated=False)
        else:
            self._schedule_collapse_check()

    def _on_reply_hover_changed(self, hovered: bool) -> None:
        self._reply_hovered = hovered
        if hovered:
            self._reply_timer.stop()
        elif self._reply_visible:
            self._reply_timer.start(1400)

    def _on_input_focus_changed(self, focused: bool) -> None:
        if focused:
            self.expand_input(animated=False)
            return
        self._schedule_collapse_check()

    def _on_preferred_width_changed(self, width: int) -> None:
        if self._expanded:
            self.quick_input_bar.setFixedWidth(width)
            self._sync_geometry(anchor_right=True)

    def _focus_input(self) -> None:
        self.expand_input(animated=True)
        self.quick_input_bar.focus_input()

    def _handle_send(self, text: str, attachments: list[str]) -> None:
        callback = self.on_send
        if callback is not None:
            callback(text, attachments)
        self.expand_input(animated=False)
        self._collapse_timer.start(1500)

    def _handle_escape(self) -> None:
        self.quick_input_bar.clear_input()
        self.quick_input_bar.clearFocus()
        self.collapse_input(animated=True, force=True)

    def expand_input(self, *, animated: bool) -> None:
        self._expanded = True
        self.quick_input_bar.setFixedWidth(self.quick_input_bar.preferred_width())
        self._set_input_reveal(self._input_target_height, animated=animated)

    def collapse_input(self, *, animated: bool, force: bool = False) -> None:
        if not force and not self._can_collapse():
            return
        self._expanded = False
        self._set_input_reveal(0, animated=animated)

    def _set_input_reveal(self, height: int, *, animated: bool) -> None:
        self._expand_animation.stop()
        if animated:
            self.quick_input_bar.show()
            self.quick_input_bar.setFixedWidth(self.quick_input_bar.preferred_width())
            self._expand_animation.setStartValue(self._input_reveal)
            self._expand_animation.setEndValue(height)
            self._expand_animation.start()
            return
        self.setInputReveal(height)
        self._handle_expand_finished()

    def _handle_expand_finished(self) -> None:
        if self._input_reveal <= 0:
            self.quick_input_bar.hide()

    def _schedule_collapse_check(self) -> None:
        self._collapse_timer.start(260)

    def _collapse_if_idle(self) -> None:
        if self._can_collapse():
            self.collapse_input(animated=True)

    def _can_collapse(self) -> bool:
        if self._avatar_hovered or self._bar_hovered or self.quick_input_bar.has_focus_within():
            return False
        return self.quick_input_bar.is_effectively_empty()

    def _sync_geometry(self, *, anchor_right: bool, right_edge: int | None = None) -> None:
        visible_widths = [self.avatar_widget.width()]
        total_height = self._margin * 2 + self.avatar_widget.height()

        if self.ignore_button.isVisible():
            visible_widths.append(self.ignore_button.sizeHint().width())
            total_height += self._gap + self.ignore_button.sizeHint().height()
        if self._input_reveal > 0:
            visible_widths.append(self.quick_input_bar.preferred_width())
            total_height += self._gap + self._input_reveal
        if self.reply_bubble.isVisible():
            bubble_hint = self.reply_bubble.sizeHint()
            visible_widths.append(min(320, bubble_hint.width()))
            total_height += self._gap + bubble_hint.height()

        total_width = self._margin * 2 + max(visible_widths)
        current_y = self.y()
        self.resize(total_width, total_height)
        if anchor_right:
            edge = right_edge if right_edge is not None else self.frameGeometry().right()
            target = self._clamp_top_left(QPoint(edge - total_width + 1, current_y))
            self.move(target)
            return
        self.move(self._clamp_top_left(self.pos()))

    def _available_geometry(self) -> QRect:
        screen = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 720)

    def _clamp_top_left(self, point: QPoint) -> QPoint:
        geometry = self._available_geometry()
        max_x = max(geometry.left(), geometry.right() - self.width())
        max_y = max(geometry.top(), geometry.bottom() - self.height())
        x = min(max(point.x(), geometry.left()), max_x)
        y = min(max(point.y(), geometry.top()), max_y)
        return QPoint(x, y)

    def _snap_to_edges(self) -> None:
        geometry = self._available_geometry()
        pos = self._clamp_top_left(self.pos())
        if abs(pos.x() - geometry.left()) <= 20:
            pos.setX(geometry.left())
        if abs((pos.x() + self.width()) - geometry.right()) <= 20:
            pos.setX(geometry.right() - self.width())
        if abs(pos.y() - geometry.top()) <= 20:
            pos.setY(geometry.top())
        if abs((pos.y() + self.height()) - geometry.bottom()) <= 20:
            pos.setY(geometry.bottom() - self.height())
        self.move(pos)

    def _save_position(self) -> None:
        callback = self.on_position_changed
        if callback is not None:
            callback(self.pos())

    def _ignore_notifications(self) -> None:
        callback = self.on_ignore_notifications
        if callback is not None and self._ignorable_count > 0:
            callback()

    def _show_context_menu(self, global_pos: QPoint | None = None) -> None:
        theme = resolve_theme(self)
        menu = QMenu(self)
        menu.setStyleSheet(
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
        open_console = QAction(tr("presence_open_console", self._language), menu)
        open_console.triggered.connect(self._open_console)
        open_jobs = QAction(tr("presence_open_jobs", self._language), menu)
        open_jobs.triggered.connect(lambda: self._open_page("jobs"))
        open_decisions = QAction(tr("presence_open_decisions", self._language), menu)
        open_decisions.triggered.connect(lambda: self._open_page("decisions"))
        if self._ignorable_count > 0:
            ignore_action = QAction(tr("presence_ignore", self._language), menu)
            ignore_action.triggered.connect(self._ignore_notifications)
            menu.addAction(ignore_action)
            menu.addSeparator()
        quiet_mode = QAction(tr("presence_quiet_mode", self._language), menu)
        quiet_mode.setCheckable(True)
        quiet_mode.setChecked(self._quiet_mode)
        quiet_mode.triggered.connect(self.set_quiet_mode)
        exit_action = QAction(tr("exit", self._language), menu)
        exit_action.triggered.connect(self._request_exit)
        menu.addAction(open_console)
        menu.addAction(open_jobs)
        menu.addAction(open_decisions)
        menu.addSeparator()
        menu.addAction(quiet_mode)
        menu.addSeparator()
        menu.addAction(exit_action)
        menu.exec(global_pos or QCursor.pos())

    def _open_console(self) -> None:
        callback = self.on_open_console
        if callback is not None:
            callback()

    def _open_page(self, page: str) -> None:
        callback = self.on_open_page
        if callback is not None:
            callback(page)

    def _request_exit(self) -> None:
        callback = self.on_exit_requested
        if callback is not None:
            callback()
