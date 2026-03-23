from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import Property, QPropertyAnimation, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from app.ui.components.chat.message_widget import DisplayMode, MessageWidget, normalize_display_mode
from app.ui.theme import FairyTheme, button_style, mix, qcolor, resolve_theme


class HoverCardFrame(MessageWidget):
    def __init__(
        self,
        parent=None,
        *,
        theme: FairyTheme | None = None,
        radius: int = 18,
        interactive: bool = True,
        display_mode: DisplayMode = "normal",
    ) -> None:
        super().__init__(parent=parent, display_mode=display_mode)
        self.theme = theme or resolve_theme(self)
        self.radius = radius
        self._interactive = interactive
        self.setObjectName("hoverCardFrame")
        self._hover_progress = 0.0
        self._base_background = self.theme.surface
        self._hover_background = mix(self.theme.surface, self.theme.fairy_blue_soft, 0.08)
        self._base_border = self.theme.divider
        self._hover_border = mix(self.theme.divider, self.theme.fairy_blue_soft, 0.38)
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setOffset(0, self.mode_metric(8, 6))
        self._shadow.setBlurRadius(self.mode_metric(24, 18))
        self._shadow.setColor(QColor(0, 0, 0, max(12, round(255 * self.theme.shadow_alpha * 0.95))))
        self.setGraphicsEffect(self._shadow)
        self._hover_animation = QPropertyAnimation(self, b"hoverProgress", self)
        self._hover_animation.setDuration(140)
        self._hover_animation.setStartValue(0.0)
        self._hover_animation.setEndValue(1.0)
        self._update_card_style()

    def set_palette(
        self,
        *,
        background: str,
        hover_background: str | None = None,
        border: str | None = None,
        hover_border: str | None = None,
    ) -> None:
        self._base_background = background
        self._hover_background = hover_background or background
        self._base_border = border or self.theme.divider
        self._hover_border = hover_border or self._base_border
        self._update_card_style()

    def _blend(self, base: str, hover: str) -> str:
        return mix(base, hover, self._hover_progress)

    def _update_shadow(self) -> None:
        self._shadow.setBlurRadius(self.mode_metric(24, 18) + self._hover_progress * self.mode_metric(10, 6))
        self._shadow.setOffset(0, self.mode_metric(8, 6) - round(self._hover_progress * 2))
        alpha = max(14, round(255 * self.theme.shadow_alpha * (0.9 + self._hover_progress * 0.35)))
        self._shadow.setColor(QColor(0, 0, 0, alpha))

    def _update_card_style(self) -> None:
        background = self._blend(self._base_background, self._hover_background)
        border = self._blend(self._base_border, self._hover_border)
        self.setStyleSheet(
            "QFrame#hoverCardFrame {"
            f"background: {background};"
            f"border: 1px solid {border};"
            f"border-radius: {self.radius}px;"
            "}"
        )
        self._update_shadow()

    def get_hover_progress(self) -> float:
        return self._hover_progress

    def set_hover_progress(self, value: float) -> None:
        self._hover_progress = max(0.0, min(1.0, float(value)))
        self._update_card_style()

    hoverProgress = Property(float, get_hover_progress, set_hover_progress)

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        if not self._interactive:
            return
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._hover_progress)
        self._hover_animation.setEndValue(1.0)
        self._hover_animation.start()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        if not self._interactive:
            return
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._hover_progress)
        self._hover_animation.setEndValue(0.0)
        self._hover_animation.start()


class AsyncImageLabel(QLabel):
    def __init__(
        self,
        parent=None,
        *,
        theme: FairyTheme | None = None,
        placeholder: str = "",
        corner_radius: int = 14,
        minimum_height: int = 120,
    ) -> None:
        super().__init__(parent)
        self.theme = theme or resolve_theme(self)
        self.corner_radius = corner_radius
        self.placeholder = placeholder
        self._pixmap = QPixmap()
        self._source = ""
        self._show_pin = False
        self._network = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self.setMinimumHeight(minimum_height)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            f"background:{mix(self.theme.surface_soft, self.theme.fairy_blue_soft, 0.08)};"
            f"border:1px solid {mix(self.theme.divider, self.theme.fairy_blue_soft, 0.18)};"
            f"border-radius:{self.corner_radius}px;"
            f"color:{self.theme.text_secondary};"
        )
        self._show_placeholder()

    def source(self) -> str:
        return self._source

    def set_marker_pin(self, enabled: bool) -> None:
        self._show_pin = bool(enabled)
        self.update()

    def set_source(self, source: str) -> None:
        self._source = (source or "").strip()
        source = self._source
        if not source:
            self._pixmap = QPixmap()
            self._show_placeholder()
            return
        if source.startswith(("http://", "https://")):
            request = QNetworkRequest(QUrl(source))
            request.setHeader(QNetworkRequest.UserAgentHeader, "Fairy/1.0 (desktop assistant)")
            if hasattr(QNetworkRequest, "RedirectPolicyAttribute") and hasattr(QNetworkRequest, "NoLessSafeRedirectPolicy"):
                request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.NoLessSafeRedirectPolicy)
            if self._reply is not None:
                self._reply.deleteLater()
            self._reply = self._network.get(request)
            self._reply.finished.connect(self._on_reply_finished)
            return
        pixmap = QPixmap(source)
        if pixmap.isNull():
            self._pixmap = QPixmap()
            self._show_placeholder()
            return
        self._pixmap = pixmap
        self._refresh_pixmap()

    def _on_reply_finished(self) -> None:
        reply = self._reply
        self._reply = None
        if reply is None:
            return
        if reply.error() == QNetworkReply.NoError:
            pixmap = QPixmap()
            pixmap.loadFromData(reply.readAll())
            self._pixmap = pixmap
            self._refresh_pixmap()
        else:
            self._pixmap = QPixmap()
            self._show_placeholder()
        reply.deleteLater()

    def _show_placeholder(self) -> None:
        self.setPixmap(QPixmap())
        self.setText(self.placeholder or "Preview")

    def _refresh_pixmap(self) -> None:
        if self._pixmap.isNull():
            self._show_placeholder()
            return
        scaled = self._pixmap.scaled(
            max(1, self.width()),
            max(1, self.height()),
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        rounded = QPixmap(scaled.size())
        rounded.fill(Qt.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(rounded.rect(), self.corner_radius, self.corner_radius)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, scaled)
        painter.end()
        self.setText("")
        self.setPixmap(rounded)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._pixmap.isNull():
            self._refresh_pixmap()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self._show_pin:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center_x = self.width() / 2
        center_y = self.height() / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(214, 64, 64, 228))
        painter.drawEllipse(round(center_x - 8), round(center_y - 17), 16, 16)
        path = QPainterPath()
        path.moveTo(center_x, center_y + 14)
        path.lineTo(center_x - 8, center_y - 5)
        path.lineTo(center_x + 8, center_y - 5)
        path.closeSubpath()
        painter.drawPath(path)
        painter.setBrush(QColor(255, 255, 255, 240))
        painter.drawEllipse(round(center_x - 3), round(center_y - 12), 6, 6)
        painter.end()


class ClickableAsyncImageLabel(AsyncImageLabel):
    clicked = Signal()

    def __init__(self, parent=None, **kwargs) -> None:
        super().__init__(parent, **kwargs)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            self.clicked.emit()


class ChipLabel(QLabel):
    def __init__(self, text: str, parent=None, *, theme: FairyTheme | None = None, tone: str = "neutral") -> None:
        super().__init__(text, parent)
        self.theme = theme or resolve_theme(self)
        self.setStyleSheet(self._chip_style(tone))

    def _chip_style(self, tone: str) -> str:
        if tone == "accent":
            background = mix(self.theme.fairy_blue_core, self.theme.fairy_blue_soft, 0.35)
            border = mix(self.theme.fairy_blue_core, self.theme.fairy_blue_soft, 0.6)
            color = "#F6FAFF"
        else:
            background = mix(self.theme.surface_soft, self.theme.fairy_blue_soft, 0.08)
            border = mix(self.theme.divider, self.theme.fairy_blue_soft, 0.22)
            color = self.theme.text_secondary
        return (
            f"background:{background};"
            f"border:1px solid {border};"
            "border-radius:10px;"
            "padding:3px 8px;"
            "font-size:11px;"
            "font-weight:700;"
            f"color:{color};"
        )


class SparklineWidget(QLabel):
    def __init__(self, values: list[float] | None = None, parent=None, *, theme: FairyTheme | None = None) -> None:
        super().__init__(parent)
        self.theme = theme or resolve_theme(self)
        self._values = list(values or [])
        self.setMinimumHeight(58)

    def set_values(self, values: list[float] | None) -> None:
        self._values = list(values or [])
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if len(self._values) < 2:
            return
        rect = self.rect().adjusted(6, 6, -6, -8)
        low = min(self._values)
        high = max(self._values)
        span = max(1.0, high - low)
        points = []
        for index, value in enumerate(self._values):
            x = rect.left() + (rect.width() * index / max(1, len(self._values) - 1))
            ratio = (float(value) - low) / span
            y = rect.bottom() - rect.height() * ratio
            points.append((x, y))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        guide_pen = QPen(qcolor(mix(self.theme.fairy_blue_soft, "#FFFFFF", 0.2)))
        guide_pen.setWidth(1)
        painter.setPen(guide_pen)
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        line_pen = QPen(qcolor(self.theme.fairy_blue_soft))
        line_pen.setWidth(3)
        painter.setPen(line_pen)
        for index in range(len(points) - 1):
            painter.drawLine(round(points[index][0]), round(points[index][1]), round(points[index + 1][0]), round(points[index + 1][1]))
        painter.end()


class ImagePreviewDialog(QDialog):
    def __init__(
        self,
        source: str,
        *,
        title: str = "",
        theme: FairyTheme | None = None,
        mark_pin: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.theme = theme or resolve_theme(parent or self)
        self.setWindowTitle(title or "Preview")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(760, 560)
        self.setStyleSheet(
            f"""
            QDialog {{
                background: {self.theme.background};
            }}
            QLabel {{
                color: {self.theme.text_primary};
            }}
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)
        title_label = QLabel(title or "Preview", self)
        title_label.setStyleSheet(f"font-size:15px; font-weight:800; color:{self.theme.text_primary};")
        close_button = QPushButton("Close", self)
        close_button.setStyleSheet(button_style(self.theme, compact=True))
        close_button.clicked.connect(self.close)
        header.addWidget(title_label)
        header.addStretch(1)
        header.addWidget(close_button)

        self.image_label = AsyncImageLabel(self, theme=self.theme, placeholder="Preview", minimum_height=460, corner_radius=20)
        self.image_label.set_marker_pin(mark_pin)
        self.image_label.set_source(source)

        root.addLayout(header)
        root.addWidget(self.image_label, 1)


def open_image_preview(
    source: str,
    *,
    title: str = "",
    theme: FairyTheme | None = None,
    mark_pin: bool = False,
    parent=None,
) -> None:
    if not (source or "").strip():
        return
    dialog = ImagePreviewDialog(source, title=title, theme=theme, mark_pin=mark_pin, parent=parent)
    dialog.exec()


def open_url(url: str) -> None:
    target = (url or "").strip()
    if not target:
        return
    if "://" not in target:
        target = "https://" + target
    QDesktopServices.openUrl(QUrl(target))


def extract_domain(url: str) -> str:
    parsed = urlparse((url or "").strip())
    return parsed.netloc or parsed.path or ""

