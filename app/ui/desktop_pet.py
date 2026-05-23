from __future__ import annotations

import html
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QInputMethodEvent,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.action_event import ActionEvent


class AvatarMode(str, Enum):
    BOOTING = "booting"
    WARMING_UP = "warming_up"
    IDLE = "idle"
    HOVER = "hover"
    LISTENING = "listening"
    THINKING = "thinking"
    ANALYZING = "analyzing"
    REPLYING = "replying"
    ERROR = "error"
    SLEEPING = "sleeping"
    DRAGGING = "dragging"


@dataclass(slots=True)
class FairyPose:
    center: QPointF
    radius: float
    aura_power: float
    ring_breath: float
    inner_breath: float
    spin: float
    dot_angle: float
    dot_orbit: float
    edge_wobble: float
    focus_x: float
    focus_y: float
    particle_alpha: int
    listening_level: float
    thinking_level: float
    warming_level: float
    analyzing_level: float
    replying_level: float
    sleeping_level: float
    error_level: float
    model_online_level: float
    model_offline_level: float
    network_online_level: float
    network_offline_level: float


class AvatarRenderer(ABC):
    """Avatar animation interface with state input."""

    def __init__(self) -> None:
        self._activity_mode = AvatarMode.BOOTING
        self._hovered = False
        self._dragging = False
        self._time = 0.0
        self._pulse = 0.0
        self._gaze = QPointF(0.0, 0.0)
        self._target_gaze = QPointF(0.0, 0.0)
        self._model_online: bool | None = None
        self._network_online: bool | None = None
        self._error_flash = 0.0

    @property
    def mode(self) -> AvatarMode:
        if self._dragging:
            return AvatarMode.DRAGGING
        if self._error_flash > 0.02:
            return AvatarMode.ERROR
        if self._activity_mode is not AvatarMode.IDLE:
            return self._activity_mode
        if self._hovered:
            return AvatarMode.HOVER
        return AvatarMode.IDLE

    @property
    def pulse(self) -> float:
        return self._pulse

    @property
    def time(self) -> float:
        return self._time

    @property
    def gaze(self) -> QPointF:
        return self._gaze

    def set_activity_mode(self, mode: AvatarMode) -> None:
        self._activity_mode = mode

    def set_hovered(self, hovered: bool) -> None:
        self._hovered = hovered
        if not hovered and self._activity_mode is AvatarMode.IDLE:
            self._target_gaze = QPointF(0.0, 0.0)

    def set_model_online(self, online: bool | None) -> None:
        self._model_online = online

    def set_network_online(self, online: bool | None) -> None:
        self._network_online = online

    def flash_error(self, strength: float = 1.0) -> None:
        self._error_flash = max(self._error_flash, strength)
        self.kick(0.9)

    def set_dragging(self, dragging: bool) -> None:
        self._dragging = dragging
        if dragging:
            self.kick(0.22)

    def set_pointer_focus(self, rect: QRectF, local_pos: QPointF | None) -> None:
        if local_pos is None:
            self._target_gaze = QPointF(0.0, 0.0)
            return

        dx = (local_pos.x() - rect.center().x()) / max(rect.width() * 0.5, 1.0)
        dy = (local_pos.y() - rect.center().y()) / max(rect.height() * 0.5, 1.0)
        self._target_gaze = QPointF(max(-1.0, min(1.0, dx)), max(-1.0, min(1.0, dy)))

    def kick(self, strength: float = 0.45) -> None:
        self._pulse = max(self._pulse, strength)

    def tick(self, dt: float) -> None:
        self._time += dt
        self._pulse = max(0.0, self._pulse - dt * 0.75)
        self._error_flash = max(0.0, self._error_flash - dt * 0.52)
        ease = min(1.0, dt * 7.0)
        self._gaze = QPointF(
            self._gaze.x() + (self._target_gaze.x() - self._gaze.x()) * ease,
            self._gaze.y() + (self._target_gaze.y() - self._gaze.y()) * ease,
        )

    @abstractmethod
    def paint(self, painter: QPainter, rect: QRectF, phase: float) -> None:
        """Draw the avatar in the given rect."""


class ChatInputLineEdit(QLineEdit):
    """QLineEdit with explicit IME composition handling."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._is_composing = False
        self.setAttribute(Qt.WA_InputMethodEnabled, True)

    @property
    def is_composing(self) -> bool:
        return self._is_composing

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        self._is_composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._is_composing and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # Enter confirms IME candidate; do not trigger returnPressed/send.
            event.ignore()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        self._is_composing = False
        super().focusOutEvent(event)


class FairyAvatar(AvatarRenderer):
    """Ring-style fairy avatar matching the provided blue emblem."""

    def __init__(self) -> None:
        super().__init__()
        self.deep_navy = QColor("#0F2A5A")
        self.navy = QColor(19, 46, 96)
        self.blue = QColor("#1F4FA3")
        self.cyan = QColor("#4F86FF")
        self.soft_blue = QColor("#8FB5FF")
        self.white_ring = QColor(236, 240, 246)
        self.dot_white = QColor(244, 247, 251)
        self.warm_hint = QColor(211, 153, 126)
        self.startup_duration = 2.8

    def paint(self, painter: QPainter, rect: QRectF, phase: float) -> None:  # noqa: ARG002
        pose = self._build_pose(rect)
        intro_t = min(1.0, self.time / max(0.1, self.startup_duration))
        if intro_t < 1.0:
            self._draw_startup_assembly(painter, pose, intro_t)
            return
        self._draw_scene_base(painter, pose)
        self._draw_outer_glow(painter, pose)
        self._draw_wobble_shell(painter, pose)
        self._draw_main_rings(painter, pose)
        self._draw_orbit_dot(painter, pose)
        self._draw_particles(painter, pose)
        self._draw_status_overlays(painter, pose)

    def _smoothstep(self, t: float) -> float:
        x = max(0.0, min(1.0, t))
        return x * x * (3.0 - 2.0 * x)

    def _draw_startup_assembly(self, painter: QPainter, pose: FairyPose, t: float) -> None:
        reveal = self._smoothstep(t)
        ring_reveal = self._smoothstep((t - 0.12) / 0.72)
        core_reveal = self._smoothstep((t - 0.30) / 0.62)
        dot_reveal = self._smoothstep((t - 0.56) / 0.44)

        painter.save()
        painter.setOpacity(0.42 + 0.58 * reveal)
        self._draw_scene_base(painter, pose)
        painter.restore()

        # Particle-like elements converge from outside to the target ring.
        painter.save()
        painter.setPen(Qt.NoPen)
        shard_count = 20
        for i in range(shard_count):
            angle = pose.spin * 1.3 + i * (math.tau / shard_count)
            target_r = pose.radius * (0.84 if i % 2 == 0 else 0.34)
            start_r = pose.radius * (1.85 + 0.22 * math.sin(i * 1.71 + self.time))
            delay = (i % 5) * 0.03
            local = self._smoothstep((t - delay) / 0.78)
            orbit_r = target_r + (start_r - target_r) * (1.0 - local)
            px = pose.center.x() + math.cos(angle) * orbit_r
            py = pose.center.y() + math.sin(angle) * orbit_r
            size = pose.radius * (0.016 + 0.010 * (1.0 - local))
            alpha = int(35 + 200 * local)
            painter.setBrush(QColor(168, 220, 255, alpha))
            painter.drawEllipse(QPointF(px, py), size, size)
        painter.restore()

        # Sweep arcs form the outer and inner rings.
        painter.save()
        start_angle = int((90 - pose.spin * 57.3) * 16)
        outer_sweep = int(360 * 16 * ring_reveal)
        outer_rect = QRectF(
            pose.center.x() - pose.radius * 0.34,
            pose.center.y() - pose.radius * 0.34,
            pose.radius * 0.68,
            pose.radius * 0.68,
        )
        outer_pen = QPen(self.white_ring)
        outer_pen.setCapStyle(Qt.RoundCap)
        outer_pen.setWidthF(max(2.0, pose.radius * 0.13))
        painter.setPen(outer_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(outer_rect, start_angle, outer_sweep)

        inner_reveal = self._smoothstep((t - 0.25) / 0.62)
        inner_sweep = int(360 * 16 * inner_reveal)
        inner_r = pose.radius * 0.20
        inner_rect = QRectF(
            pose.center.x() - inner_r,
            pose.center.y() - inner_r,
            inner_r * 2,
            inner_r * 2,
        )
        inner_pen = QPen(QColor(150, 206, 255, int(70 + 160 * inner_reveal)))
        inner_pen.setCapStyle(Qt.RoundCap)
        inner_pen.setWidthF(max(1.5, pose.radius * 0.046))
        painter.setPen(inner_pen)
        painter.drawArc(inner_rect, start_angle - int(45 * 16), inner_sweep)
        painter.restore()

        # Light trails converge, similar to boot-style elemental composition.
        painter.save()
        for i in range(4):
            a = pose.spin + i * (math.pi / 2)
            sx = pose.center.x() + math.cos(a) * pose.radius * 1.25
            sy = pose.center.y() + math.sin(a) * pose.radius * 1.25
            tx = pose.center.x() + math.cos(a) * pose.radius * (0.18 + 0.16 * (1.0 - ring_reveal))
            ty = pose.center.y() + math.sin(a) * pose.radius * (0.18 + 0.16 * (1.0 - ring_reveal))
            cx = sx + (tx - sx) * ring_reveal
            cy = sy + (ty - sy) * ring_reveal
            trail_pen = QPen(QColor(116, 198, 255, int(55 + 160 * ring_reveal)))
            trail_pen.setCapStyle(Qt.RoundCap)
            trail_pen.setWidthF(max(1.5, pose.radius * 0.024))
            painter.setPen(trail_pen)
            painter.drawLine(QPointF(sx, sy), QPointF(cx, cy))
        painter.restore()

        # Core appears after the ring starts to form.
        if core_reveal > 0.0:
            painter.save()
            core_radius = pose.radius * 0.145 * core_reveal
            core = QRadialGradient(pose.center, max(1.0, pose.radius * 0.16 * core_reveal))
            core.setColorAt(0.0, QColor(27, 104, 218))
            core.setColorAt(1.0, QColor(6, 50, 134))
            painter.setBrush(QBrush(core))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(pose.center, core_radius, core_radius)
            painter.restore()

        # Orbit dot slides into position near the end of startup.
        if dot_reveal > 0.0:
            painter.save()
            moving_angle = pose.dot_angle - (1.0 - dot_reveal) * 1.35
            moving_orbit = pose.dot_orbit + pose.radius * 0.42 * (1.0 - dot_reveal)
            dot_center = QPointF(
                pose.center.x() + math.cos(moving_angle) * moving_orbit,
                pose.center.y() + math.sin(moving_angle) * moving_orbit,
            )
            glow = QRadialGradient(dot_center, pose.radius * 0.19 * dot_reveal)
            glow.setColorAt(0.0, QColor(240, 247, 255, int(170 * dot_reveal)))
            glow.setColorAt(1.0, QColor(170, 225, 255, 0))
            painter.setBrush(QBrush(glow))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(dot_center, pose.radius * 0.19 * dot_reveal, pose.radius * 0.19 * dot_reveal)
            painter.setBrush(QBrush(self.dot_white))
            painter.setPen(QPen(QColor(225, 236, 255), 1.0))
            painter.drawEllipse(dot_center, pose.radius * 0.105 * dot_reveal, pose.radius * 0.105 * dot_reveal)
            painter.restore()

        # Blend into the steady-state avatar for a smooth handover.
        painter.save()
        painter.setOpacity(reveal * 0.95)
        self._draw_outer_glow(painter, pose)
        self._draw_wobble_shell(painter, pose)
        self._draw_main_rings(painter, pose)
        if dot_reveal >= 0.95:
            self._draw_orbit_dot(painter, pose)
        self._draw_particles(painter, pose)
        painter.restore()

    def _build_pose(self, rect: QRectF) -> FairyPose:
        mode = self.mode
        hover = 1.0 if mode is AvatarMode.HOVER else 0.0
        listening = 1.0 if mode is AvatarMode.LISTENING else 0.0
        thinking = 1.0 if mode is AvatarMode.THINKING else 0.0
        warming = 1.0 if mode is AvatarMode.WARMING_UP else 0.0
        analyzing = 1.0 if mode is AvatarMode.ANALYZING else 0.0
        replying = 1.0 if mode is AvatarMode.REPLYING else 0.0
        sleeping = 1.0 if mode is AvatarMode.SLEEPING else 0.0
        error = 1.0 if mode is AvatarMode.ERROR else 0.0
        dragging = 1.0 if mode is AvatarMode.DRAGGING else 0.0
        model_online = 1.0 if self._model_online is True else 0.0
        model_offline = 1.0 if self._model_online is False else 0.0
        network_online = 1.0 if self._network_online is True else 0.0
        network_offline = 1.0 if self._network_online is False else 0.0

        focus_x = self.gaze.x() * 10.0
        focus_y = self.gaze.y() * 8.0
        radius = min(rect.width(), rect.height()) / 2.08
        bob = math.sin(self.time * (1.02 + listening * 0.08 + analyzing * 0.12)) * (
            1.6
            + replying * 0.9
            + warming * 0.5
            + analyzing * 0.4
            + self.pulse * 0.9
            - sleeping * 0.8
        )
        center = QPointF(rect.center().x() + focus_x * 0.08, rect.center().y() - 2 + bob)
        aura_power = (
            0.56
            + hover * 0.16
            + listening * 0.20
            + thinking * 0.34
            + warming * 0.28
            + analyzing * 0.38
            + replying * 0.24
            + dragging * 0.18
            + error * 0.28
            + model_online * 0.12
            - sleeping * 0.22
            - model_offline * 0.18
            + self.pulse * 0.40
        )
        ring_breath = 1.0 + math.sin(
            self.time * (1.6 + thinking * 0.6 + warming * 0.45 + analyzing * 0.9 + replying * 0.3 - sleeping * 0.7)
        ) * (0.014 + aura_power * 0.010)
        inner_breath = 1.0 + math.cos(
            self.time * (1.2 + replying * 1.1 + warming * 0.35 + analyzing * 0.4 - sleeping * 0.5)
        ) * (0.011 + aura_power * 0.008)
        spin = self.time * (0.32 + thinking * 0.30 + warming * 0.38 + analyzing * 0.48 + replying * 0.25 - sleeping * 0.14)
        dot_angle = math.pi * 0.26 + math.sin(self.time * (0.62 + replying * 0.2 + analyzing * 0.16)) * 0.06
        dot_orbit = radius * (0.245 + hover * 0.004 + replying * 0.010 + warming * 0.006 + analyzing * 0.010)
        edge_wobble = 0.011 + thinking * 0.010 + warming * 0.010 + analyzing * 0.014 + dragging * 0.006 + self.pulse * 0.006
        particle_alpha = int(34 + min(1.0, max(0.0, aura_power)) * 66 - sleeping * 12 + network_online * 12)

        return FairyPose(
            center=center,
            radius=radius,
            aura_power=aura_power,
            ring_breath=ring_breath,
            inner_breath=inner_breath,
            spin=spin,
            dot_angle=dot_angle,
            dot_orbit=dot_orbit,
            edge_wobble=edge_wobble,
            focus_x=focus_x,
            focus_y=focus_y,
            particle_alpha=particle_alpha,
            listening_level=listening,
            thinking_level=thinking,
            warming_level=warming,
            analyzing_level=analyzing,
            replying_level=replying,
            sleeping_level=sleeping,
            error_level=error,
            model_online_level=model_online,
            model_offline_level=model_offline,
            network_online_level=network_online,
            network_offline_level=network_offline,
        )

    def _draw_scene_base(self, painter: QPainter, pose: FairyPose) -> None:
        base = QRadialGradient(pose.center, pose.radius * 1.04)
        base.setColorAt(0.0, QColor(32, 74, 142))
        base.setColorAt(0.62, QColor(24, 61, 122))
        base.setColorAt(1.0, QColor(15, 42, 92))
        painter.setBrush(QBrush(base))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(pose.center, pose.radius * 0.97, pose.radius * 0.97)

        core_haze = QRadialGradient(pose.center, pose.radius * 0.88)
        core_haze.setColorAt(0.0, QColor(143, 181, 255, 20 + int(8 * pose.listening_level)))
        core_haze.setColorAt(0.72, QColor(110, 151, 230, 7))
        core_haze.setColorAt(1.0, QColor(45, 92, 168, 0))
        painter.setBrush(QBrush(core_haze))
        painter.drawEllipse(pose.center, pose.radius * 0.88, pose.radius * 0.88)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(227, 235, 246, 150), max(1.0, pose.radius * 0.011)))
        painter.drawEllipse(pose.center, pose.radius * 0.98, pose.radius * 0.98)

    def _draw_outer_glow(self, painter: QPainter, pose: FairyPose) -> None:
        glow_radius = pose.radius * (1.05 + pose.aura_power * 0.14)
        glow = QRadialGradient(pose.center, glow_radius)
        glow.setColorAt(0.0, QColor(154, 196, 255, 8 + int(6 * pose.replying_level)))
        glow.setColorAt(0.46, QColor(96, 150, 226, int(22 + pose.aura_power * 18)))
        glow.setColorAt(1.0, QColor(56, 108, 182, 0))
        painter.setBrush(QBrush(glow))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(pose.center, glow_radius, glow_radius)

        ring_pen = QPen(QColor(179, 205, 238, int(24 + pose.aura_power * 16)))
        ring_pen.setWidthF(max(1.0, pose.radius * 0.008))
        painter.setPen(ring_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(pose.center, pose.radius * 1.04, pose.radius * 1.04)

    def _draw_wobble_shell(self, painter: QPainter, pose: FairyPose) -> None:
        points: list[QPointF] = []
        num_points = 64
        base_radius = pose.radius * 0.70
        for i in range(num_points):
            theta = (i / num_points) * math.tau
            wave = (
                0.68 * math.cos(theta * 8 + pose.spin * 0.55)
                + 0.18 * math.sin(theta * 16 - pose.spin * 0.42)
            )
            r = base_radius * (1.0 + pose.edge_wobble * wave)
            x = pose.center.x() + math.cos(theta) * r
            y = pose.center.y() + math.sin(theta) * r
            points.append(QPointF(x, y))

        shell = QPainterPath()
        shell.addPolygon(QPolygonF(points))
        shell.closeSubpath()

        shell_grad = QRadialGradient(pose.center, pose.radius * 0.78)
        shell_grad.setColorAt(0.0, QColor(14, 36, 92))
        shell_grad.setColorAt(0.82, QColor(10, 27, 70))
        shell_grad.setColorAt(1.0, QColor(8, 21, 54))
        painter.setBrush(QBrush(shell_grad))
        painter.setPen(QPen(QColor(82, 122, 188, 24), 1.0))
        painter.drawPath(shell)

    def _draw_main_rings(self, painter: QPainter, pose: FairyPose) -> None:
        painter.setBrush(QBrush(QColor(24, 60, 128, 34)))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(pose.center, pose.radius * 0.58, pose.radius * 0.58)

        outer_ring_pen = QPen(QColor(230, 235, 243, 236))
        outer_ring_pen.setCapStyle(Qt.RoundCap)
        outer_ring_pen.setWidthF(max(2.0, pose.radius * 0.120))
        painter.setPen(outer_ring_pen)
        painter.setBrush(Qt.NoBrush)
        outer_radius = pose.radius * (0.34 * pose.ring_breath)
        painter.drawEllipse(pose.center, outer_radius, outer_radius)

        inner_ring_pen = QPen(QColor(177, 195, 228, 194))
        inner_ring_pen.setCapStyle(Qt.RoundCap)
        inner_ring_pen.setWidthF(max(1.4, pose.radius * 0.050))
        painter.setPen(inner_ring_pen)
        inner_r = pose.radius * 0.175 * pose.inner_breath
        painter.drawEllipse(
            QPointF(
                pose.center.x() + pose.focus_x * 0.015,
                pose.center.y() + pose.focus_y * 0.015,
            ),
            inner_r,
            inner_r,
        )

        core = QRadialGradient(pose.center, pose.radius * 0.16)
        core.setColorAt(0.0, QColor(36, 95, 194))
        core.setColorAt(0.32, QColor(19, 68, 149))
        core.setColorAt(1.0, QColor(10, 35, 90))
        painter.setBrush(QBrush(core))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(pose.center, pose.radius * 0.145, pose.radius * 0.145)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(140, 188, 232, 76), max(1.0, pose.radius * 0.010)))
        painter.drawEllipse(pose.center, pose.radius * 0.148, pose.radius * 0.148)

    def _draw_orbit_dot(self, painter: QPainter, pose: FairyPose) -> None:
        dot_center = QPointF(
            pose.center.x() + math.cos(pose.dot_angle) * pose.dot_orbit + pose.focus_x * 0.08,
            pose.center.y() + math.sin(pose.dot_angle) * pose.dot_orbit + pose.focus_y * 0.08,
        )

        glow = QRadialGradient(dot_center, pose.radius * 0.16)
        glow.setColorAt(0.0, QColor(232, 238, 246, 104 + int(18 * pose.replying_level)))
        glow.setColorAt(1.0, QColor(136, 178, 214, 0))
        painter.setBrush(QBrush(glow))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(dot_center, pose.radius * 0.16, pose.radius * 0.16)

        painter.setBrush(QBrush(self.dot_white))
        painter.setPen(QPen(QColor(214, 224, 238), 1.0 + pose.analyzing_level * 0.22))
        dot_radius = pose.radius * (0.090 + pose.replying_level * 0.008 + pose.error_level * 0.006)
        painter.drawEllipse(dot_center, dot_radius, dot_radius)

    def _draw_particles(self, painter: QPainter, pose: FairyPose) -> None:
        particle_count = 7 + int(pose.aura_power * 4 + pose.analyzing_level * 4 + pose.network_online_level * 2)
        painter.save()
        painter.setPen(Qt.NoPen)
        for index in range(particle_count):
            angle = pose.spin + index * (math.tau / particle_count)
            orbit = pose.radius * (0.84 + 0.05 * math.sin(self.time * (1.8 + pose.analyzing_level * 0.4) + index))
            px = pose.center.x() + math.cos(angle) * orbit
            py = pose.center.y() + math.sin(angle * 1.3) * orbit * 0.6
            dot_radius = 1.1 + 1.4 * (0.5 + 0.5 * math.sin(self.time * 2.4 + index + pose.replying_level * 0.6))
            alpha = int(pose.particle_alpha * (0.38 + 0.62 * (0.5 + 0.5 * math.sin(self.time * 1.5 + index))))
            painter.setBrush(QColor(194, 232, 255, alpha))
            painter.drawEllipse(QPointF(px, py), dot_radius, dot_radius)
        painter.restore()

    def _draw_status_overlays(self, painter: QPainter, pose: FairyPose) -> None:
        if pose.warming_level > 0.0:
            painter.save()
            painter.setBrush(Qt.NoBrush)
            for idx, offset in enumerate((0.0, 0.45)):
                wave_t = (self.time * (0.22 + idx * 0.03) + offset) % 1.0
                alpha = int((1.0 - wave_t) * 88 * pose.warming_level)
                pen = QPen(QColor(182, 224, 255, alpha))
                pen.setWidthF(max(1.4, pose.radius * 0.018))
                painter.setPen(pen)
                rr = pose.radius * (0.50 + wave_t * 0.24)
                painter.drawEllipse(pose.center, rr, rr)
            painter.restore()

        if pose.analyzing_level > 0.0:
            painter.save()
            painter.setBrush(Qt.NoBrush)
            halo = QRadialGradient(pose.center, pose.radius * 1.06)
            halo.setColorAt(0.0, QColor(142, 195, 255, 0))
            halo.setColorAt(0.7, QColor(142, 195, 255, int(34 * pose.analyzing_level)))
            halo.setColorAt(1.0, QColor(142, 195, 255, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(halo))
            painter.drawEllipse(pose.center, pose.radius * 1.06, pose.radius * 1.06)
            pulse_pen = QPen(QColor(190, 228, 255, int(96 * pose.analyzing_level)))
            pulse_pen.setWidthF(max(1.8, pose.radius * 0.020))
            painter.setPen(pulse_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(pose.center, pose.radius * 0.86, pose.radius * 0.86)
            painter.restore()

        if pose.replying_level > 0.0:
            painter.save()
            painter.setBrush(Qt.NoBrush)
            for idx in range(2):
                wave_t = (self.time * (1.5 + idx * 0.3)) % 1.0
                alpha = int((1.0 - wave_t) * 90 * pose.replying_level)
                pen = QPen(QColor(186, 228, 255, alpha))
                pen.setWidthF(max(1.4, pose.radius * 0.018))
                painter.setPen(pen)
                rr = pose.radius * (0.46 + wave_t * 0.22)
                painter.drawEllipse(pose.center, rr, rr)
            painter.restore()

        if pose.model_offline_level > 0.0:
            painter.save()
            pen = QPen(QColor(132, 168, 210, 76))
            pen.setWidthF(max(1.2, pose.radius * 0.012))
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(pose.center, pose.radius * 0.64, pose.radius * 0.64)
            painter.restore()

        if pose.network_offline_level > 0.0:
            painter.save()
            pen = QPen(QColor(94, 124, 170, 62))
            pen.setWidthF(max(1.2, pose.radius * 0.010))
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(pose.center, pose.radius * 0.93, pose.radius * 0.93)
            painter.restore()


from PySide6.QtWidgets import QFrame, QStackedWidget

from app.system_notifications.notification_presenter import notification_counts
from app.ui.components.context_side_panel import ContextSidePanel
from app.ui.components.navigation_sidebar import NavigationSidebar
from app.ui.components.system_notifications_panel import SystemNotificationsPanel
from app.ui.components.top_status_bar import TopStatusBar
from app.ui.components.chat import ChatMessage
from app.ui.i18n import current_ui_language, localize_mode_label, normalize_ui_language, tr
from app.ui.pages.chat_page import ChatPage
from app.ui.pages.debug_page import DebugPage
from app.ui.pages.decisions_page import DecisionsPage
from app.ui.pages.jobs_page import JobsPage
from app.ui.pages.knowledge_page import KnowledgePage
from app.ui.pages.settings_page import SettingsPage
from app.ui.theme import apply_soft_shadow, card_style, mix, resolve_theme, rgba


class AvatarOrbWidget(QWidget):
    def __init__(self, avatar: AvatarRenderer | None = None, parent=None) -> None:
        super().__init__(parent)
        self._avatar = avatar or FairyAvatar()
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.setFixedSize(196, 196)
        self.setMouseTracking(True)

    def _tick(self) -> None:
        self._phase += 0.05
        self._avatar.tick(0.033)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(8.0, 8.0, self.width() - 16.0, self.height() - 16.0)
        self._avatar.paint(painter, rect, self._phase)
        painter.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._avatar.set_pointer_focus(QRectF(0.0, 0.0, float(self.width()), float(self.height())), event.position())
        self.update()
        super().mouseMoveEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._avatar.set_hovered(True)
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._avatar.set_hovered(False)
        self._avatar.set_pointer_focus(QRectF(0.0, 0.0, float(self.width()), float(self.height())), None)
        self.update()
        super().leaveEvent(event)

    def set_activity_mode(self, mode: AvatarMode) -> None:
        self._avatar.set_activity_mode(mode)
        self.update()

    def set_model_online(self, online: bool | None) -> None:
        self._avatar.set_model_online(online)
        self.update()

    def set_network_online(self, online: bool | None) -> None:
        self._avatar.set_network_online(online)
        self.update()

    def kick(self, strength: float = 0.45) -> None:
        self._avatar.kick(strength)
        self.update()

    def flash_error(self, strength: float = 1.0) -> None:
        self._avatar.flash_error(strength)
        self.update()


class DesktopPetWindow(QWidget):
    PAGE_KEYS = {
        "chat": ("page_chat_title", "page_chat_subtitle"),
        "knowledge": ("page_knowledge_title", "page_knowledge_subtitle"),
        "decisions": ("page_decisions_title", "page_decisions_subtitle"),
        "jobs": ("page_jobs_title", "page_jobs_subtitle"),
        "debug": ("page_debug_title", "page_debug_subtitle"),
        "settings": ("page_settings_title", "page_settings_subtitle"),
    }

    def __init__(self, avatar: AvatarRenderer | None = None, parent=None) -> None:
        super().__init__(parent)
        self._theme = resolve_theme(self)
        self._language = current_ui_language()
        self.setWindowTitle("Fairy")
        self.resize(1360, 880)
        self.setMinimumSize(1160, 760)
        self.setStyleSheet(
            f"""
            QWidget {{
                color: {self._theme.text_primary};
                font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
            }}
            """
        )

        self._avatar_renderer = avatar or FairyAvatar()
        self._avatar_widget = AvatarOrbWidget(self._avatar_renderer, self)
        self._pending_attachments: list[str] = []
        self._runtime_snapshot: dict[str, object] = {}
        self._retrieval_debug_snapshot: dict[str, object] | None = None
        self._pending_decisions: list[dict[str, object]] = []
        self._confirmed_decisions: list[dict[str, object]] = []
        self._rejected_decisions: list[dict[str, object]] = []
        self._system_notifications: list[dict[str, object]] = []
        self._knowledge_snapshot: dict[str, object] = {}
        self._reindex_jobs: list[dict[str, object]] = []
        self._promoted_reindex_jobs: list[dict[str, object]] = []
        self._reindex_snapshot: dict[str, object] | None = None
        self._current_task = ""
        self._current_page = "chat"
        self._mode_badge = "NORMAL"
        self._route_name = "local"
        self._route_reason = ""
        self._tool_name = ""
        self._tool_detail = ""
        self._runtime_status_text = ""
        self._system_state = "booting"
        self._system_status_text = ""
        self._voice_active = False
        self._runtime_provider = "local_server"
        self._runtime_model = ""
        self._analysis_route = "local"
        self._context_panel_visible = True
        self._system_panel_visible = False
        self._closing = False

        self.on_user_message: Callable[[str, list[str]], None] | None = None
        self.on_close_request: Callable[[], bool | None] | None = None
        self.on_open_settings: Callable[[], None] | None = None
        self.on_pending_decision_action: Callable[[str, str], None] | None = None
        self.on_reindex_job_action: Callable[[str, str], None] | None = None
        self.on_reindex_requested: Callable[[], None] | None = None
        self.on_notification_action: Callable[[str, str], None] | None = None

        self._build_shell()
        self._bind_signals()
        self.set_ui_language(self._language)
        self._switch_page("chat")
        self._sync_shell_state()

    def _build_shell(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        shell = QFrame(self)
        shell.setObjectName("appShell")
        shell.setStyleSheet(
            f"""
            QFrame#appShell {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {rgba(self._theme.background, 252)},
                    stop:0.55 {rgba(self._theme.surface_soft, 248)},
                    stop:1 {rgba(mix(self._theme.surface_soft, self._theme.fairy_blue_soft, 0.05), 248)});
                border: 1px solid {rgba(self._theme.divider, 242)};
                border-radius: 28px;
            }}
            """
        )
        apply_soft_shadow(shell, self._theme, blur=38, y_offset=12, strength=1.1)
        root.addWidget(shell, 1)

        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(14, 14, 14, 14)
        shell_layout.setSpacing(14)

        self.sidebar = NavigationSidebar(shell)
        self.sidebar.setMinimumWidth(220)
        self.sidebar.setMaximumWidth(240)
        self.sidebar.set_header_widget(self._avatar_widget)
        shell_layout.addWidget(self.sidebar, 0)

        center_wrap = QFrame(shell)
        center_wrap.setObjectName("centerWrap")
        center_wrap.setStyleSheet(f"QFrame#centerWrap {{ background: transparent; border: none; }} QLabel {{ color:{self._theme.text_primary}; }}")
        center_layout = QVBoxLayout(center_wrap)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(12)

        self.top_bar = TopStatusBar(center_wrap)
        center_layout.addWidget(self.top_bar, 0)

        self.notifications_panel = SystemNotificationsPanel(center_wrap)
        self.notifications_panel.hide()
        center_layout.addWidget(self.notifications_panel, 0)

        self.page_stack = QStackedWidget(center_wrap)
        self.chat_page = ChatPage(self.page_stack)
        self.knowledge_page = KnowledgePage(self.page_stack)
        self.decisions_page = DecisionsPage(self.page_stack)
        self.jobs_page = JobsPage(self.page_stack)
        self.debug_page = DebugPage(self.page_stack)
        self.settings_page = SettingsPage(self.page_stack)
        self.page_stack.addWidget(self.chat_page)
        self.page_stack.addWidget(self.knowledge_page)
        self.page_stack.addWidget(self.decisions_page)
        self.page_stack.addWidget(self.jobs_page)
        self.page_stack.addWidget(self.debug_page)
        self.page_stack.addWidget(self.settings_page)
        center_layout.addWidget(self.page_stack, 1)

        self.footer_label = QLabel("", center_wrap)
        self.footer_label.setStyleSheet(
            f"QLabel {{ background: {rgba(self._theme.surface, 244)}; border:1px solid {rgba(self._theme.divider, 236)}; "
            f"border-radius:16px; padding:10px 12px; color:{self._theme.text_secondary}; font-size:11px; }}"
        )
        apply_soft_shadow(self.footer_label, self._theme, blur=18, y_offset=8, strength=0.75)
        center_layout.addWidget(self.footer_label, 0)
        shell_layout.addWidget(center_wrap, 1)

        self.context_panel = ContextSidePanel(shell)
        shell_layout.addWidget(self.context_panel, 0)

    def _bind_signals(self) -> None:
        self.sidebar.on_page_selected = self._switch_page
        self.top_bar.system_button.clicked.connect(self._toggle_system_panel)
        self.top_bar.context_button.clicked.connect(self._toggle_context_panel)
        self.top_bar.settings_button.clicked.connect(self._open_settings)
        self.top_bar.close_button.clicked.connect(self._request_close)
        self.chat_page.on_send = self._handle_chat_send
        self.decisions_page.on_action = self._forward_pending_decision_action
        self.jobs_page.on_job_action = self._forward_job_action
        self.jobs_page.on_reindex_requested = self._forward_reindex_request
        self.settings_page.on_open_settings = self._open_settings
        self.notifications_panel.on_action = self._forward_notification_action
        self.notifications_panel.on_open_related = self._open_notification_target

    def set_ui_language(self, language: str) -> None:
        self._language = normalize_ui_language(language)
        self.sidebar.set_ui_language(self._language)
        self.top_bar.set_ui_language(self._language)
        self.chat_page.set_ui_language(self._language)
        self.settings_page.set_ui_language(self._language)
        self._switch_page(self._current_page)
        self._sync_shell_state()

    def _page_meta(self, page: str) -> tuple[str, str]:
        title_key, subtitle_key = self.PAGE_KEYS.get(page, self.PAGE_KEYS["chat"])
        return tr(title_key, self._language), tr(subtitle_key, self._language)

    def _switch_page(self, page: str) -> None:
        self._current_page = page
        pages = {
            "chat": self.chat_page,
            "knowledge": self.knowledge_page,
            "decisions": self.decisions_page,
            "jobs": self.jobs_page,
            "debug": self.debug_page,
            "settings": self.settings_page,
        }
        widget = pages.get(page, self.chat_page)
        self.page_stack.setCurrentWidget(widget)
        self.sidebar.set_current_page(page)
        title, subtitle = self._page_meta(page)
        self.top_bar.set_page(title, subtitle)
        self._update_context_panel()

    def open_page(self, page: str) -> None:
        self._switch_page(page)

    def _toggle_context_panel(self) -> None:
        self._context_panel_visible = not self._context_panel_visible
        self.context_panel.setVisible(self._context_panel_visible)

    def _toggle_system_panel(self) -> None:
        self._system_panel_visible = not self._system_panel_visible
        self.notifications_panel.setVisible(self._system_panel_visible)

    def _handle_chat_send(self, text: str, attachments: list[str]) -> None:
        outgoing_lines: list[str] = []
        if text:
            outgoing_lines.append(text)
        if attachments:
            names = [path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for path in attachments]
            outgoing_lines.append(tr("chat_uploaded_attachments", self._language, names=", ".join(names)))
        if outgoing_lines:
            self._append_chat(tr("chat_user_speaker", self._language), "\n".join(outgoing_lines))
        self._avatar_widget.kick(0.8)
        self._avatar_widget.set_activity_mode(AvatarMode.LISTENING)
        callback = self.on_user_message
        if callback is not None:
            callback(text, attachments)

    def _append_chat(self, speaker: str, text: str, rich_text: bool = False) -> None:
        self.chat_page.push_text_message(speaker, text, rich_text=rich_text)

    def append_chat_message(self, message: ChatMessage) -> None:
        self.chat_page.push_message(message)

    def update_chat_message(self, message_id: str, *, text: str | None = None, payload: dict | None = None) -> bool:
        updated = self.chat_page.update_message(message_id, text=text, payload=payload)
        self._avatar_widget.kick(0.35)
        return updated

    def show_assistant_chat_message(self, message: ChatMessage) -> None:
        self.append_chat_message(message)
        self._avatar_widget.kick(0.7)

    def _forward_pending_decision_action(self, action: str, item_id: str) -> None:
        callback = self.on_pending_decision_action
        if callback is not None:
            callback(action, item_id)

    def _forward_job_action(self, action: str, job_id: str) -> None:
        callback = self.on_reindex_job_action
        if callback is not None:
            callback(action, job_id)

    def _forward_reindex_request(self) -> None:
        callback = self.on_reindex_requested
        if callback is not None:
            callback()

    def _forward_notification_action(self, action: str, notification_id: str) -> None:
        callback = self.on_notification_action
        if callback is not None:
            callback(action, notification_id)

    def _open_notification_target(self, entity_type: str, entity_id: str) -> None:
        self._system_panel_visible = False
        self.notifications_panel.hide()
        if entity_type == "reindex_job":
            self._switch_page("jobs")
            self.jobs_page.select_job(entity_id)
            return
        if entity_type == "decision":
            self._switch_page("decisions")
            self.decisions_page.select_item(entity_id)
            return
        if entity_type == "settings":
            self._switch_page("settings")

    def _open_settings(self) -> None:
        callback = self.on_open_settings
        if callback is not None and not self._closing:
            callback()

    def _request_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.close()

    def clear_session_trace(self) -> None:
        self.debug_page.clear()
        self._retrieval_debug_snapshot = None
        self._update_context_panel()

    def set_current_task(self, text: str) -> None:
        self._current_task = text.strip()
        self.chat_page.set_current_task(text)
        self._sync_shell_state()

    def append_action_event(self, event: ActionEvent) -> None:
        self.debug_page.append_action_event(event)
        if event.name == "rag_retrieval_visualized":
            self.set_retrieval_debug_snapshot(event.payload)
        self._update_context_panel()

    def append_terminal_output(self, stream_name: str, text: str) -> None:
        self.debug_page.append_terminal_output(stream_name, text)

    def show_session_artifacts(self, changed_files: list[str], commands_run: list[str], validations: list[dict[str, object]]) -> None:
        self.debug_page.show_session_artifacts(changed_files, commands_run, validations)
        self._update_context_panel()

    def set_retrieval_debug_snapshot(self, snapshot: dict[str, object] | None) -> None:
        self._retrieval_debug_snapshot = dict(snapshot or {}) if snapshot else None
        self.debug_page.set_retrieval_snapshot(snapshot)
        self._update_context_panel()

    def clear_retrieval_debug_snapshot(self) -> None:
        self._retrieval_debug_snapshot = None
        self.debug_page.set_retrieval_snapshot(None)
        self._update_context_panel()

    def set_pending_decisions(self, items: list[dict[str, object]]) -> None:
        self._pending_decisions = [dict(item) for item in items]
        self.sidebar.set_badge_count("decisions", len(self._pending_decisions), tone="pending")
        self.decisions_page.set_snapshot(self._pending_decisions, self._confirmed_decisions, self._rejected_decisions)
        self._sync_shell_state()

    def set_system_notifications(self, items: list[dict[str, object]]) -> None:
        self._system_notifications = [dict(item) for item in items]
        self.notifications_panel.set_notifications(self._system_notifications)
        self._sync_shell_state()

    def set_decision_library(self, *, confirmed: list[dict[str, object]], rejected: list[dict[str, object]]) -> None:
        self._confirmed_decisions = [dict(item) for item in confirmed]
        self._rejected_decisions = [dict(item) for item in rejected]
        self.decisions_page.set_snapshot(self._pending_decisions, self._confirmed_decisions, self._rejected_decisions)
        self._update_context_panel()

    def set_knowledge_snapshot(self, snapshot: dict[str, object]) -> None:
        self._knowledge_snapshot = dict(snapshot)
        self.knowledge_page.set_snapshot(snapshot)
        self._update_context_panel()

    def set_reindex_jobs(self, items: list[dict[str, object]]) -> None:
        self._reindex_jobs = [dict(item) for item in items]
        running_jobs = sum(1 for item in self._reindex_jobs if str(item.get("status", "")) in {"queued", "running", "cancel_requested"})
        self.sidebar.set_badge_count("jobs", running_jobs, tone="warning")
        self.jobs_page.set_jobs(self._reindex_jobs)
        self._sync_shell_state()

    def set_promoted_reindex_jobs(self, items: list[dict[str, object]]) -> None:
        self._promoted_reindex_jobs = [dict(item) for item in items]
        self.jobs_page.set_promoted_history(self._promoted_reindex_jobs)
        self._update_context_panel()

    def set_reindex_status(self, snapshot: dict[str, object] | None) -> None:
        self._reindex_snapshot = dict(snapshot or {}) if snapshot else None
        self._update_context_panel()

    def set_agent_route(self, skill_name: str, reason: str = "") -> None:
        self._route_name = skill_name.strip() or self._analysis_route or "local"
        self._route_reason = reason.strip()
        self._sync_shell_state()

    def set_mode_badge(self, text: str) -> None:
        self._mode_badge = text.strip() or "NORMAL"
        self._sync_shell_state()

    def set_tool_status(self, tool_name: str = "", detail: str = "") -> None:
        self._tool_name = tool_name.strip()
        self._tool_detail = detail.strip()
        self._sync_shell_state()

    def set_runtime_status(self, text: str) -> None:
        self._runtime_status_text = text.strip()
        self._sync_shell_state()

    def set_runtime_snapshot(self, snapshot: dict[str, object] | None) -> None:
        self._runtime_snapshot = dict(snapshot or {})
        self._runtime_provider = str(self._runtime_snapshot.get("provider_id", "") or self._runtime_provider or "local_server")
        self._runtime_model = str(self._runtime_snapshot.get("model", "") or self._runtime_model)
        self._analysis_route = str(self._runtime_snapshot.get("analysis_route", "") or self._analysis_route or "local")
        self._avatar_widget.set_model_online(self._coerce_optional_bool(self._runtime_snapshot.get("model_online")))
        self._avatar_widget.set_network_online(self._coerce_optional_bool(self._runtime_snapshot.get("network_online")))
        self._sync_shell_state()

    def set_settings_summary(self, summary: dict[str, object]) -> None:
        self.settings_page.set_summary(summary)
        self._update_context_panel()

    def set_system_state(self, state: str, status_text: str = "") -> None:
        self._system_state = state.strip() or "idle"
        self._system_status_text = status_text.strip()
        mode_map = {
            "booting": AvatarMode.BOOTING,
            "warming_up": AvatarMode.WARMING_UP,
            "idle": AvatarMode.IDLE,
            "error": AvatarMode.ERROR,
            "sleeping": AvatarMode.SLEEPING,
        }
        self._avatar_widget.set_activity_mode(mode_map.get(self._system_state, AvatarMode.IDLE))
        self._sync_shell_state()

    def show_assistant_message(self, text: str, rich_text: bool = False) -> None:
        self.show_assistant_chat_message(ChatMessage.from_text_payload("Fairy", text, rich_text=rich_text))

    def set_voice_active(self, active: bool) -> None:
        self._voice_active = active
        if active:
            self._avatar_widget.set_activity_mode(AvatarMode.REPLYING)
            self._avatar_widget.kick(0.35)
        else:
            self._restore_avatar_mode()
        self._sync_shell_state()

    def set_voice_progress(self, payload: dict[str, object] | None) -> None:
        if not payload:
            return
        text = str(payload.get("text", "") or "").strip()
        if text:
            self.footer_label.setText(f"VOICE · {text}")

    def start_processing(self, analyzing: bool = False) -> None:
        self._avatar_widget.set_activity_mode(AvatarMode.ANALYZING if analyzing else AvatarMode.THINKING)
        self._avatar_widget.kick(0.8)
        self.footer_label.setText("ANALYZING..." if analyzing else "THINKING...")

    def start_thinking(self) -> None:
        self.start_processing(analyzing=False)

    def stop_thinking(self) -> None:
        self._restore_avatar_mode()
        self._sync_shell_state()

    def flash_error_state(self, status_text: str = "连接异常") -> None:
        self._avatar_widget.flash_error()
        self.footer_label.setText(status_text)

    def show_assistant_typing(self, text: str) -> None:
        self.show_assistant_message(text, rich_text=False)

    def prepare_for_shutdown(self) -> None:
        self.on_user_message = None
        self.on_open_settings = None
        self.on_pending_decision_action = None
        self.on_reindex_job_action = None
        self.on_reindex_requested = None

    def closeEvent(self, event) -> None:  # noqa: N802
        callback = self.on_close_request
        if callback is not None:
            should_close = callback()
            if should_close is False:
                self._closing = False
                event.ignore()
                return
        self.prepare_for_shutdown()
        self.on_close_request = None
        event.accept()

    def _restore_avatar_mode(self) -> None:
        if self._voice_active:
            self._avatar_widget.set_activity_mode(AvatarMode.REPLYING)
            return
        state_map = {
            "booting": AvatarMode.BOOTING,
            "warming_up": AvatarMode.WARMING_UP,
            "idle": AvatarMode.IDLE,
            "sleeping": AvatarMode.SLEEPING,
            "error": AvatarMode.ERROR,
        }
        self._avatar_widget.set_activity_mode(state_map.get(self._system_state, AvatarMode.IDLE))

    def _sync_shell_state(self) -> None:
        running_jobs = sum(1 for item in self._reindex_jobs if str(item.get("status", "")) in {"queued", "running", "cancel_requested"})
        notification_stats = notification_counts(self._system_notifications)
        fingerprint = str(
            self._runtime_snapshot.get("active_fingerprint")
            or self._runtime_snapshot.get("active_embedding_fingerprint")
            or self._knowledge_snapshot.get("active_fingerprint")
            or ""
        )
        backend = str(
            self._runtime_snapshot.get("vector_backend")
            or self._knowledge_snapshot.get("vector_backend")
            or "sqlite"
        )
        provider = self._runtime_provider or "local_server"
        model = self._runtime_model
        route = self._analysis_route or self._route_name or "local"
        self.top_bar.set_status(
            mode_label=self._mode_badge,
            provider_label=provider,
            model_label=model,
            route_label=route,
            backend_label=backend,
            fingerprint_label=fingerprint,
            pending_count=len(self._pending_decisions),
            running_jobs=running_jobs,
            system_count=notification_stats.get("active", 0),
            system_action_required=notification_stats.get("action_required", 0),
            system_critical=notification_stats.get("critical", 0),
        )
        summary = self._build_chat_summary(provider=provider, model=model, backend=backend, fingerprint=fingerprint)
        self.chat_page.set_runtime_summary(
            mode_label=self._mode_badge,
            route_label=route,
            pending_decisions=len(self._pending_decisions),
            summary=summary,
        )
        footer_bits = [self._runtime_status_text, self._system_status_text]
        if self._tool_name:
            tool_line = self._tool_name
            if self._tool_detail:
                tool_line += f" · {self._tool_detail}"
            footer_bits.append(tool_line)
        if self._current_task:
            footer_bits.append(f"task={self._current_task}")
        footer_text = " · ".join(part for part in footer_bits if part)
        self.footer_label.setText(footer_text or tr("footer_idle", self._language))
        self._update_context_panel()

    def _build_chat_summary(self, *, provider: str, model: str, backend: str, fingerprint: str) -> str:
        notification_stats = notification_counts(self._system_notifications)
        is_zh = self._language == "zh_CN"
        parts = [localize_mode_label(self._mode_badge, self._language)]
        if fingerprint:
            parts.append(f"记忆就绪 ({fingerprint[:18]})" if is_zh else f"memory ready ({fingerprint[:18]})")
        else:
            parts.append("记忆就绪" if is_zh else "memory ready")
        if self._pending_decisions:
            parts.append(
                f"{len(self._pending_decisions)} 个待确认决策" if is_zh else f"{len(self._pending_decisions)} decisions pending"
            )
        if notification_stats.get("critical", 0):
            parts.append(
                f"{notification_stats.get('critical', 0)} 个严重告警"
                if is_zh
                else f"{notification_stats.get('critical', 0)} critical alerts"
            )
        elif notification_stats.get("action_required", 0):
            parts.append(
                f"{notification_stats.get('action_required', 0)} 个待操作事项"
                if is_zh
                else f"{notification_stats.get('action_required', 0)} actions waiting"
            )
        elif notification_stats.get("active", 0):
            parts.append(
                f"{notification_stats.get('active', 0)} 个系统事项"
                if is_zh
                else f"{notification_stats.get('active', 0)} system tasks"
            )
        if self._route_name and self._route_reason:
            parts.append(f"路由 {self._route_name}" if is_zh else f"route {self._route_name}")
        parts.append(f"提供方 {provider}" if is_zh else f"provider {provider}")
        parts.append(f"后端 {backend}" if is_zh else f"backend {backend}")
        if model:
            parts.append(f"模型 {model}" if is_zh else f"model {model}")
        return " · ".join(parts)

    def _update_context_panel(self) -> None:
        page = self._current_page
        if page == "chat":
            self.context_panel.set_context("Session Context", "Current mode, route, and knowledge pressure.", self._build_chat_context_html())
        elif page == "knowledge":
            self.context_panel.set_context("Knowledge Signals", "Active backend and recent structured items.", self._build_knowledge_context_html())
        elif page == "decisions":
            self.context_panel.set_context("Decision Context", "Pending work should stay visible until it is resolved.", self._build_decisions_context_html())
        elif page == "jobs":
            self.context_panel.set_context("Job Operations", "Index migrations and their health live here.", self._build_jobs_context_html())
        elif page == "debug":
            self.context_panel.set_context("Prompt Assembly", "What retrieval found versus what the model actually received.", self._build_debug_context_html())
        else:
            self.context_panel.set_context("Configuration Summary", "Core runtime settings at a glance.", self._build_settings_context_html())

    def _build_chat_context_html(self) -> str:
        theme = self._theme
        retrieval = self._retrieval_debug_snapshot or {}
        hits = int(retrieval.get("result_count", 0) or 0)
        injected = int(retrieval.get("injected_context_item_count", 0) or 0)
        preview = str(retrieval.get("final_injected_context_preview", "") or "").strip()
        preview_html = html.escape(preview[:220]).replace("\n", "<br>")
        return (
            f"<div><b>route</b>: {html.escape(self._analysis_route or self._route_name or 'local')}</div>"
            f"<div><b>provider</b>: {html.escape(self._runtime_provider or 'local_server')}</div>"
            f"<div><b>model</b>: {html.escape(self._runtime_model or '-')}</div>"
            f"<div><b>pending decisions</b>: {len(self._pending_decisions)}</div>"
            f"<div><b>retrieval hits</b>: {hits}</div>"
            f"<div><b>injected items</b>: {injected}</div>"
            f"<div style='margin-top:10px; color:{theme.text_secondary};'><b>preview</b></div>"
            f"<div style='margin-top:4px;'>{preview_html or 'No retrieval context injected this turn.'}</div>"
        )

    def _build_knowledge_context_html(self) -> str:
        theme = self._theme
        total = int(self._knowledge_snapshot.get("total", 0) or 0)
        return (
            f"<div><b>active fingerprint</b>: {html.escape(str(self._knowledge_snapshot.get('active_fingerprint', '') or '-'))}</div>"
            f"<div><b>backend</b>: {html.escape(str(self._knowledge_snapshot.get('vector_backend', '') or 'sqlite'))}</div>"
            f"<div><b>visible items</b>: {total}</div>"
            f"<div style='margin-top:10px; color:{theme.text_secondary};'>This page shows the durable layer, not every raw log fragment. That distinction matters.</div>"
        )

    def _build_decisions_context_html(self) -> str:
        theme = self._theme
        return (
            f"<div><b>pending</b>: {len(self._pending_decisions)}</div>"
            f"<div><b>confirmed</b>: {len(self._confirmed_decisions)}</div>"
            f"<div><b>rejected</b>: {len(self._rejected_decisions)}</div>"
            f"<div style='margin-top:10px; color:{theme.text_secondary};'>Confirmed decisions enter the active knowledge layer. Pending ones remain visible until a human says yes or no.</div>"
        )

    def _build_jobs_context_html(self) -> str:
        latest = self._reindex_snapshot or {}
        notification_stats = notification_counts(self._system_notifications)
        health_summary = latest.get("health_check_summary", {}) if isinstance(latest.get("health_check_summary"), dict) else {}
        warnings = list(health_summary.get("warnings") or [])
        reasons = list(health_summary.get("reasons") or [])
        health_state = "pass" if bool(latest.get("health_check_passed", False)) else "needs review"
        return (
            f"<div><b>jobs tracked</b>: {len(self._reindex_jobs)}</div>"
            f"<div><b>promoted history</b>: {len(self._promoted_reindex_jobs)}</div>"
            f"<div><b>system tasks</b>: {notification_stats.get('active', 0)}</div>"
            f"<div><b>latest status</b>: {html.escape(str(latest.get('status', '') or '—'))}</div>"
            f"<div><b>promotion</b>: {html.escape(str(latest.get('promotion_status', '') or '—'))}</div>"
            f"<div><b>health</b>: {html.escape(health_state)}</div>"
            f"<div><b>warnings</b>: {len(warnings)} · <b>reasons</b>: {len(reasons)}</div>"
            f"<div><b>collection</b>: {html.escape(str(latest.get('output_collection_name', '') or '—'))}</div>"
            f"<div><b>backend</b>: {html.escape(str(latest.get('backend_type', '') or '—'))}</div>"
        )

    def _build_debug_context_html(self) -> str:
        theme = self._theme
        snapshot = self._retrieval_debug_snapshot or {}
        preview = str(snapshot.get("final_injected_context_preview", "") or "").strip()
        preview_html = html.escape(preview[:520]).replace("\n", "<br>")
        return (
            f"<div><b>rag triggered</b>: {html.escape(str(snapshot.get('triggered', False)))}</div>"
            f"<div><b>vector backend</b>: {html.escape(str(snapshot.get('vector_backend', '') or '-'))}</div>"
            f"<div><b>collection</b>: {html.escape(str(snapshot.get('collection_name', '') or '-'))}</div>"
            f"<div><b>chars</b>: {html.escape(str(snapshot.get('injected_context_chars', 0)))} / {html.escape(str(snapshot.get('max_context_chars', 0)))}"
            f" · truncated={html.escape(str(snapshot.get('injected_context_truncated', False)))}</div>"
            f"<div style='margin-top:10px; color:{theme.text_secondary};'><b>final injected context</b></div>"
            f"<div style='margin-top:4px;'>{preview_html or 'This turn did not inject retrieved context.'}</div>"
        )

    def _build_settings_context_html(self) -> str:
        theme = self._theme
        return (
            f"<div><b>mode</b>: {html.escape(self._mode_badge)}</div>"
            f"<div><b>provider</b>: {html.escape(self._runtime_provider or 'local_server')}</div>"
            f"<div><b>model</b>: {html.escape(self._runtime_model or '-')}</div>"
            f"<div><b>status</b>: {html.escape(self._runtime_status_text or '-')}</div>"
            f"<div style='margin-top:10px; color:{theme.text_secondary};'>Full configuration still lives in the dedicated settings dialog. This page is the structured summary, not a second config maze.</div>"
        )

    def _coerce_optional_bool(self, value: object) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
        return None








