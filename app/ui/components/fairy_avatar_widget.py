from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

from app.ui.desktop_pet import AvatarMode, AvatarRenderer, FairyAvatar
from app.ui.theme import mix, qcolor, resolve_theme


STATE_PRIORITY = {
    "critical": 60,
    "action_required": 50,
    "thinking": 40,
    "busy": 30,
    "notify": 20,
    "idle": 10,
    "sleep": 0,
}


class FairyAvatarWidget(QWidget):
    clicked = Signal()
    doubleClicked = Signal()
    contextMenuRequested = Signal(QPoint)
    hoverChanged = Signal(bool)

    def __init__(self, avatar: AvatarRenderer | None = None, *, size: int = 80, parent=None) -> None:
        super().__init__(parent)
        self._avatar = avatar or FairyAvatar()
        self._avatar_size = max(64, min(112, int(size)))
        self._phase = 0.0
        self._status = "idle"
        self._quiet_mode = False
        self._badge_count = 0
        self._status_title = "Fairy is resting"
        self._status_summary = "Structure is calm. Waiting for the next step."

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setToolTipDuration(4000)
        self.setFixedSize(self._avatar_size + 16, self._avatar_size + 16)

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._refresh_tooltip()

    @property
    def status(self) -> str:
        return self._status

    def set_presence_state(
        self,
        status: str,
        *,
        badge_count: int = 0,
        quiet_mode: bool | None = None,
        status_title: str = "",
        status_summary: str = "",
    ) -> None:
        normalized = (status or "idle").strip().lower()
        if normalized not in STATE_PRIORITY:
            normalized = "idle"
        self._status = normalized
        self._badge_count = max(0, int(badge_count or 0))
        if quiet_mode is not None:
            self._quiet_mode = bool(quiet_mode)
        if status_title.strip():
            self._status_title = status_title.strip()
        if status_summary.strip():
            self._status_summary = status_summary.strip()

        if normalized == "thinking":
            self._avatar.set_activity_mode(AvatarMode.THINKING)
        elif normalized == "sleep":
            self._avatar.set_activity_mode(AvatarMode.SLEEPING)
        elif normalized == "busy":
            self._avatar.set_activity_mode(AvatarMode.WARMING_UP)
        else:
            self._avatar.set_activity_mode(AvatarMode.IDLE)
        self._refresh_tooltip()
        self.update()

    def set_quiet_mode(self, enabled: bool) -> None:
        self._quiet_mode = bool(enabled)
        self._refresh_tooltip()
        self.update()

    def _tick(self) -> None:
        self._phase += 0.04
        self._avatar.tick(0.033)
        self.update()

    def _avatar_rect(self) -> QRectF:
        return QRectF(8.0, 8.0, float(self._avatar_size), float(self._avatar_size))

    def _refresh_tooltip(self) -> None:
        quiet_suffix = " - Quiet Mode" if self._quiet_mode else ""
        self.setToolTip(f"{self._status_title}{quiet_suffix}\n{self._status_summary}")

    def paintEvent(self, event) -> None:  # noqa: N802, ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = self._avatar_rect()
        self._draw_bottom_glow(painter, rect)
        self._draw_status_energy(painter, rect)
        self._avatar.paint(painter, rect, self._phase)
        self._draw_badge(painter, rect)
        painter.end()

    def _draw_bottom_glow(self, painter: QPainter, rect: QRectF) -> None:
        color = self._state_color()
        center = QPointF(rect.center().x(), rect.bottom() - 4.0)
        radius = rect.width() * 0.72
        glow = QRadialGradient(center, radius)
        alpha = 28 if self._quiet_mode else 42
        if self._status == "sleep":
            alpha = 14
        glow.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), alpha))
        glow.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(rect.center().x(), rect.bottom() - 6.0), radius, radius * 0.32)

    def _draw_status_energy(self, painter: QPainter, rect: QRectF) -> None:
        color = self._state_color()
        pulse = 0.5 + 0.5 * math.sin(self._phase * 0.9)
        quiet_scale = 0.55 if self._quiet_mode else 1.0

        painter.save()
        painter.setBrush(Qt.NoBrush)

        if self._status == "idle":
            self._draw_ring(
                painter,
                rect.adjusted(-1.5, -1.5, 1.5, 1.5),
                color,
                alpha=int((62 + pulse * 22) * quiet_scale),
                width=2.6,
            )
        elif self._status == "thinking":
            for offset in (0.0, 0.5):
                wave = (self._phase * 0.13 + offset) % 1.0
                alpha = int((1.0 - wave) * 110 * quiet_scale)
                expand = 2.0 + wave * 12.0
                self._draw_ring(
                    painter,
                    rect.adjusted(-expand, -expand, expand, expand),
                    color,
                    alpha=alpha,
                    width=3.2,
                )
        elif self._status == "busy":
            self._draw_ring(
                painter,
                rect.adjusted(-2.0, -2.0, 2.0, 2.0),
                color,
                alpha=int((88 + pulse * 18) * quiet_scale),
                width=3.4,
            )
            self._draw_ring(
                painter,
                rect.adjusted(-6.0, -6.0, 6.0, 6.0),
                color,
                alpha=int((26 + pulse * 10) * quiet_scale),
                width=2.0,
            )
        elif self._status == "notify":
            self._draw_ring(
                painter,
                rect.adjusted(-2.0, -2.0, 2.0, 2.0),
                color,
                alpha=int(112 * quiet_scale),
                width=3.0,
            )
        elif self._status == "action_required":
            self._draw_ring(
                painter,
                rect.adjusted(-2.5, -2.5, 2.5, 2.5),
                color,
                alpha=int((150 + pulse * 28) * quiet_scale),
                width=3.8,
            )
        elif self._status == "critical":
            warm = qcolor(mix(color, self._warm_hint(), 0.18))
            self._draw_ring(
                painter,
                rect.adjusted(-2.5, -2.5, 2.5, 2.5),
                warm,
                alpha=int((168 + pulse * 24) * quiet_scale),
                width=4.0,
            )
            self._draw_ring(
                painter,
                rect.adjusted(-7.0, -7.0, 7.0, 7.0),
                warm,
                alpha=int((44 + pulse * 18) * quiet_scale),
                width=2.2,
            )
        else:
            self._draw_ring(
                painter,
                rect.adjusted(-1.0, -1.0, 1.0, 1.0),
                color,
                alpha=int(42 * quiet_scale),
                width=2.2,
            )

        painter.restore()

    def _draw_ring(self, painter: QPainter, rect: QRectF, color: QColor, *, alpha: int, width: float) -> None:
        pen = QPen(QColor(color.red(), color.green(), color.blue(), max(0, min(255, alpha))))
        pen.setWidthF(width)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawEllipse(rect)

    def _draw_badge(self, painter: QPainter, rect: QRectF) -> None:
        if self._status not in {"notify", "action_required", "critical"}:
            return
        badge_radius = 9.0 if self._badge_count <= 9 else 11.0
        center = QPointF(rect.right() - 4.0, rect.top() + 8.0)
        color = self._state_color()
        painter.save()
        painter.setPen(QPen(QColor(255, 255, 255, 210), 1.2))
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 236 if not self._quiet_mode else 176))
        painter.drawEllipse(center, badge_radius, badge_radius)
        if self._badge_count > 0:
            label = "9+" if self._badge_count > 9 else str(self._badge_count)
            painter.setPen(QColor(10, 17, 28))
            font = painter.font()
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            text_rect = QRectF(
                center.x() - badge_radius,
                center.y() - badge_radius,
                badge_radius * 2.0,
                badge_radius * 2.0,
            )
            painter.drawText(text_rect, Qt.AlignCenter, label)
        painter.restore()

    def _state_color(self) -> QColor:
        theme = resolve_theme(self)
        if self._status == "critical":
            return qcolor(mix(theme.fairy_blue_glow, theme.warm_hint, 0.16))
        if self._status == "action_required":
            return qcolor(mix(theme.fairy_blue_glow, theme.fairy_blue_soft, 0.30))
        if self._status == "notify":
            return qcolor(theme.fairy_blue_soft)
        if self._status == "thinking":
            return qcolor(theme.fairy_blue_glow)
        if self._status == "busy":
            return qcolor(mix(theme.fairy_blue_core, theme.fairy_blue_soft, 0.34))
        if self._status == "sleep":
            return qcolor(mix(theme.fairy_blue_deep, theme.fairy_blue_soft, 0.20))
        return qcolor(theme.fairy_blue_core)

    def _warm_hint(self) -> QColor:
        theme = resolve_theme(self)
        return qcolor(theme.warm_hint)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._avatar.set_hovered(True)
        self.hoverChanged.emit(True)
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._avatar.set_hovered(False)
        self._avatar.set_pointer_focus(self._avatar_rect(), None)
        self.hoverChanged.emit(False)
        self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._avatar.set_pointer_focus(self._avatar_rect(), event.position())
        self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        elif event.button() == Qt.RightButton:
            self.contextMenuRequested.emit(event.globalPosition().toPoint())
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)
