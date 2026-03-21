from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout

from app.ui.components.chat.base_card_widget import HoverCardFrame, SparklineWidget
from app.ui.components.chat.chat_message import ChatMessage
from app.ui.components.chat.message_widget import DisplayMode
from app.ui.theme import mix, resolve_theme


class WeatherCardWidget(HoverCardFrame):
    def __init__(
        self,
        message: ChatMessage,
        parent=None,
        *,
        language: str = "zh",
        display_mode: DisplayMode = "normal",
    ) -> None:
        theme = resolve_theme(parent)
        super().__init__(parent, theme=theme, radius=18 if display_mode == "normal" else 16, interactive=True, display_mode=display_mode)
        self.message = message
        self.language = language
        self.setMaximumWidth(self.mode_metric(360, 300))

        root = QVBoxLayout(self)
        root.setContentsMargins(
            self.mode_metric(18, 14),
            self.mode_metric(16, 12),
            self.mode_metric(18, 14),
            self.mode_metric(16, 12),
        )
        root.setSpacing(self.mode_metric(10, 8))

        header = QHBoxLayout()
        header.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(2)

        self.city_label = QLabel(self)
        self.city_label.setStyleSheet(
            f"font-size:{self.mode_metric(16, 14)}px; font-weight:800; color:#F7FBFF; border:none; background:transparent;"
        )
        self.condition_label = QLabel(self)
        self.condition_label.setStyleSheet(
            f"font-size:{self.mode_metric(12, 11)}px; font-weight:700; color:#D6E6FF; border:none; background:transparent;"
        )
        left.addWidget(self.city_label)
        left.addWidget(self.condition_label)

        self.icon_label = QLabel(self)
        self.icon_label.setStyleSheet(
            f"font-size:{self.mode_metric(30, 24)}px; color:#FFFFFF; border:none; background:transparent;"
        )

        header.addLayout(left, 1)
        header.addWidget(self.icon_label)

        hero = QHBoxLayout()
        hero.setSpacing(10)
        self.temp_label = QLabel(self)
        self.temp_label.setStyleSheet(
            f"font-size:{self.mode_metric(46, 36)}px; font-weight:900; color:#FFFFFF; border:none; background:transparent;"
        )
        self.range_label = QLabel(self)
        self.range_label.setStyleSheet(
            f"font-size:{self.mode_metric(13, 11)}px; font-weight:700; color:#DBEAFF; border:none; background:transparent;"
        )
        hero_text = QVBoxLayout()
        hero_text.setSpacing(4)
        hero_text.addStretch(1)
        hero_text.addWidget(self.range_label)
        hero_text.addStretch(1)
        hero.addWidget(self.temp_label, 0)
        hero.addLayout(hero_text, 1)

        stats = QGridLayout()
        stats.setHorizontalSpacing(10)
        stats.setVerticalSpacing(6)
        self.feels_label = QLabel(self)
        self.extra_label = QLabel(self)
        self.wind_label = QLabel(self)
        for label in (self.feels_label, self.extra_label, self.wind_label):
            label.setStyleSheet(
                f"font-size:{self.mode_metric(12, 11)}px; font-weight:600; color:#E2ECFF; border:none; background:transparent;"
            )
        stats.addWidget(self.feels_label, 0, 0)
        stats.addWidget(self.extra_label, 0, 1)
        stats.addWidget(self.wind_label, 1, 0, 1, 2)

        self.sparkline = SparklineWidget(parent=self, theme=theme)
        self.sparkline.setMinimumHeight(self.mode_metric(54, 42))
        self.sparkline.setStyleSheet("border:none; background:transparent;")

        root.addLayout(header)
        root.addLayout(hero)
        root.addLayout(stats)
        root.addWidget(self.sparkline)
        self.update_message(message)

    def _update_card_style(self) -> None:
        top = self._blend("#0B1F3A", mix(self.theme.fairy_blue_deep, "#08172A", 0.16))
        bottom = self._blend(
            mix(self.theme.fairy_blue_core, "#14345C", 0.42),
            mix(self.theme.fairy_blue_core, self.theme.fairy_blue_glow, 0.34),
        )
        border = self._blend(
            mix(self.theme.fairy_blue_soft, "#FFFFFF", 0.18),
            mix(self.theme.fairy_blue_soft, "#FFFFFF", 0.42),
        )
        self.setStyleSheet(
            "QFrame#hoverCardFrame {"
            f"background:qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {top}, stop:1 {bottom});"
            f"border:1px solid {border};"
            f"border-radius:{self.radius}px;"
            "}"
        )
        self._update_shadow()

    def update_message(self, message: ChatMessage) -> None:
        self.message = message
        payload = message.payload
        city = str(payload.get("city", "") or ("Weather" if self.language.startswith("en") else "\u5929\u6c14")).strip()
        temp = payload.get("temp", "--")
        high = payload.get("high", "--")
        low = payload.get("low", "--")
        feels_like = payload.get("feels_like", "--")
        wind = str(payload.get("wind", "--") or "--").strip()
        condition = str(payload.get("condition", "") or ("Current" if self.language.startswith("en") else "\u5f53\u524d")).strip()
        icon_type = str(payload.get("icon_type", "") or "").strip()
        summary = str(payload.get("summary", "") or "").strip()

        self.city_label.setText(city)
        self.condition_label.setText(condition)
        self.temp_label.setText(f"{temp}\N{DEGREE SIGN}")
        if self.language.startswith("en"):
            self.range_label.setText(f"H {high}\N{DEGREE SIGN}   L {low}\N{DEGREE SIGN}")
            self.feels_label.setText(f"Feels {feels_like}\N{DEGREE SIGN}")
            self.extra_label.setText(condition)
            self.wind_label.setText(f"Wind {wind}")
        else:
            self.range_label.setText(f"\u9ad8 {high}\N{DEGREE SIGN}   \u4f4e {low}\N{DEGREE SIGN}")
            self.feels_label.setText(f"\u4f53\u611f {feels_like}\N{DEGREE SIGN}")
            self.extra_label.setText(condition)
            self.wind_label.setText(f"\u98ce\u901f {wind}")
        self.icon_label.setText(self._icon_for_condition(icon_type or condition))

        curve = payload.get("hourly_curve")
        can_show_curve = isinstance(curve, list) and len(curve) >= 2 and not self.is_compact()
        self.sparkline.setVisible(can_show_curve)
        if can_show_curve:
            self.sparkline.set_values([float(item) for item in curve if isinstance(item, (int, float))])
        else:
            self.sparkline.set_values([])

        if summary and self.is_compact():
            compact_summary = summary[:61].rstrip()
            if len(summary) > 61:
                compact_summary += "..."
            self.wind_label.setText(compact_summary)

    def _icon_for_condition(self, condition: str) -> str:
        lowered = condition.lower()
        if any(token in lowered for token in ("rain", "shower", "\u96e8")):
            return "\U0001F327"
        if any(token in lowered for token in ("cloud", "overcast", "\u9634", "\u4e91")):
            return "\u2601"
        if any(token in lowered for token in ("snow", "\u96ea")):
            return "\u2744"
        if any(token in lowered for token in ("storm", "thunder", "\u96f7")):
            return "\u26c8"
        if any(token in lowered for token in ("fog", "mist", "\u96fe")):
            return "\U0001F32B"
        return "\u2600"
