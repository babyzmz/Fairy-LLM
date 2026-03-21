from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QWidget


@dataclass(frozen=True)
class FairyTheme:
    scheme: str
    fairy_blue_deep: str
    fairy_blue_core: str
    fairy_blue_glow: str
    fairy_blue_soft: str
    background: str
    surface: str
    surface_soft: str
    divider: str
    text_primary: str
    text_secondary: str
    success: str
    warning: str
    error: str
    warm_hint: str
    shadow_alpha: float


LIGHT_THEME = FairyTheme(
    scheme="light",
    fairy_blue_deep="#0F2A5A",
    fairy_blue_core="#1F4FA3",
    fairy_blue_glow="#4F86FF",
    fairy_blue_soft="#8FB5FF",
    background="#F4F6F8",
    surface="#FFFFFF",
    surface_soft="#F0F2F5",
    divider="#E3E6EB",
    text_primary="#1A1C20",
    text_secondary="#6B7280",
    success="#6E9E8C",
    warning="#B48752",
    error="#BB6B79",
    warm_hint="#C49073",
    shadow_alpha=0.06,
)

DARK_THEME = FairyTheme(
    scheme="dark",
    fairy_blue_deep="#0F2A5A",
    fairy_blue_core="#1F4FA3",
    fairy_blue_glow="#4F86FF",
    fairy_blue_soft="#8FB5FF",
    background="#1C1E22",
    surface="#25282E",
    surface_soft="#2C3037",
    divider="#3A3F48",
    text_primary="#ECEFF4",
    text_secondary="#9AA1AC",
    success="#84B49E",
    warning="#C79A65",
    error="#CD8A94",
    warm_hint="#D69B83",
    shadow_alpha=0.35,
)


def resolve_theme(widget: QWidget | None = None) -> FairyTheme:
    override = os.getenv("FAIRY_THEME", "").strip().lower()
    if override == "light":
        return LIGHT_THEME
    if override == "dark":
        return DARK_THEME

    hints = QGuiApplication.styleHints()
    if hints is not None and hasattr(Qt, "ColorScheme"):
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return LIGHT_THEME
        if scheme == Qt.ColorScheme.Dark:
            return DARK_THEME

    app = QApplication.instance()
    palette = widget.palette() if widget is not None else (app.palette() if app is not None else None)
    if palette is not None:
        return DARK_THEME if palette.window().color().lightness() < 128 else LIGHT_THEME
    return DARK_THEME


def qcolor(value: str | QColor) -> QColor:
    return value if isinstance(value, QColor) else QColor(value)


def rgba(value: str | QColor, alpha: int | float) -> str:
    color = qcolor(value)
    alpha_value = alpha
    if isinstance(alpha, float) and alpha <= 1.0:
        alpha_value = round(alpha * 255)
    alpha_int = max(0, min(255, int(alpha_value)))
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha_int})"


def mix(color_a: str | QColor, color_b: str | QColor, amount: float) -> str:
    amount = max(0.0, min(1.0, amount))
    a = qcolor(color_a)
    b = qcolor(color_b)
    mixed = QColor(
        round(a.red() + (b.red() - a.red()) * amount),
        round(a.green() + (b.green() - a.green()) * amount),
        round(a.blue() + (b.blue() - a.blue()) * amount),
    )
    return mixed.name()


def apply_soft_shadow(widget: QWidget, theme: FairyTheme, *, blur: int = 24, y_offset: int = 8, strength: float = 1.0) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(max(1.0, blur * strength))
    effect.setOffset(0, y_offset)
    effect.setColor(QColor(0, 0, 0, max(0, min(255, round(255 * theme.shadow_alpha * strength)))))
    widget.setGraphicsEffect(effect)


def tone_colors(theme: FairyTheme, tone: str) -> tuple[str, str, str]:
    if tone == "active":
        fg = theme.text_primary if theme.scheme == "light" else theme.fairy_blue_soft
        bg = rgba(mix(theme.surface_soft, theme.fairy_blue_soft, 0.16), 245)
        border = rgba(mix(theme.divider, theme.fairy_blue_soft, 0.55), 245)
        return fg, bg, border
    if tone in {"info", "running"}:
        fg = mix(theme.text_primary, theme.fairy_blue_soft, 0.46)
        bg = rgba(mix(theme.surface_soft, theme.fairy_blue_soft, 0.12), 240)
        border = rgba(mix(theme.divider, theme.fairy_blue_soft, 0.42), 235)
        return fg, bg, border
    if tone in {"success", "confirmed"}:
        fg = theme.success
        bg = rgba(mix(theme.surface_soft, theme.success, 0.12), 238)
        border = rgba(mix(theme.divider, theme.success, 0.44), 232)
        return fg, bg, border
    if tone in {"warning", "pending"}:
        fg = theme.warning
        bg = rgba(mix(theme.surface_soft, theme.warning, 0.12), 238)
        border = rgba(mix(theme.divider, theme.warning, 0.42), 232)
        return fg, bg, border
    if tone in {"error", "rejected"}:
        fg = theme.error
        bg = rgba(mix(theme.surface_soft, theme.error, 0.12), 238)
        border = rgba(mix(theme.divider, theme.error, 0.42), 232)
        return fg, bg, border
    fg = theme.text_secondary
    bg = rgba(theme.surface_soft, 236)
    border = rgba(theme.divider, 240)
    return fg, bg, border


def card_style(theme: FairyTheme, selector: str = "QFrame", *, radius: int = 18, soft: bool = False, accent: bool = False) -> str:
    background = theme.surface_soft if soft else theme.surface
    border = mix(theme.divider, theme.fairy_blue_soft, 0.22) if accent else theme.divider
    return (
        f"{selector} {{"
        f"background: {rgba(background, 248 if theme.scheme == 'light' else 244)};"
        f"border: 1px solid {rgba(border, 242)};"
        f"border-radius: {radius}px;"
        "}"
    )


def button_style(theme: FairyTheme, tone: str = "neutral", *, radius: int = 12, compact: bool = False) -> str:
    padding = "6px 12px" if compact else "8px 14px"
    if tone == "accent":
        bg = mix(theme.fairy_blue_core, theme.fairy_blue_glow, 0.18)
        hover = mix(theme.fairy_blue_core, theme.fairy_blue_glow, 0.36)
        border = mix(theme.fairy_blue_core, theme.fairy_blue_soft, 0.46)
        return (
            "QPushButton {"
            f"color: {theme.surface}; background: {bg}; border: 1px solid {border};"
            f"border-radius: {radius}px; padding: {padding}; font-size: 12px; font-weight: 700;"
            "}"
            "QPushButton:hover {"
            f"background: {hover}; border-color: {theme.fairy_blue_glow};"
            "}"
            "QPushButton:pressed {"
            f"background: {theme.fairy_blue_core};"
            "}"
            "QPushButton:disabled {"
            f"color: {rgba(theme.surface, 180)}; background: {rgba(theme.fairy_blue_core, 100)}; border-color: {rgba(theme.fairy_blue_soft, 120)};"
            "}"
        )
    if tone == "danger":
        bg = rgba(mix(theme.surface_soft, theme.error, 0.18), 248)
        hover = rgba(mix(theme.surface_soft, theme.error, 0.28), 248)
        border = mix(theme.divider, theme.error, 0.48)
        return (
            "QPushButton {"
            f"color: {theme.error}; background: {bg}; border: 1px solid {border};"
            f"border-radius: {radius}px; padding: {padding}; font-size: 12px; font-weight: 700;"
            "}"
            "QPushButton:hover {"
            f"background: {hover};"
            "}"
            "QPushButton:disabled {"
            f"color: {rgba(theme.text_secondary, 170)}; background: {rgba(theme.surface_soft, 180)}; border-color: {rgba(theme.divider, 180)};"
            "}"
        )
    bg = rgba(theme.surface_soft, 236 if theme.scheme == "light" else 224)
    hover = rgba(mix(theme.surface_soft, theme.fairy_blue_soft, 0.08), 246)
    border = rgba(theme.divider, 236)
    return (
        "QPushButton {"
        f"color: {theme.text_primary}; background: {bg}; border: 1px solid {border};"
        f"border-radius: {radius}px; padding: {padding}; font-size: 12px; font-weight: 600;"
        "}"
        "QPushButton:hover {"
        f"background: {hover}; border-color: {rgba(mix(theme.divider, theme.fairy_blue_soft, 0.28), 242)};"
        "}"
        "QPushButton:focus {"
        f"border-color: {theme.fairy_blue_glow};"
        "}"
        "QPushButton:disabled {"
        f"color: {rgba(theme.text_secondary, 170)}; background: {rgba(theme.surface_soft, 180)}; border-color: {rgba(theme.divider, 180)};"
        "}"
    )


def line_edit_style(
    theme: FairyTheme,
    selector: str = "QLineEdit",
    *,
    radius: int = 12,
    frosted: bool = False,
    padding: str = "10px 12px",
) -> str:
    background = rgba("#FFFFFF", 191) if frosted and theme.scheme == "light" else (
        rgba("#282A30", 191) if frosted else rgba(theme.surface_soft, 238)
    )
    return (
        f"{selector} {{"
        f"background: {background}; color: {theme.text_primary}; border: 1px solid {rgba(theme.divider, 230)};"
        f"border-radius: {radius}px; padding: {padding}; font-size: 13px;"
        f"selection-background-color: {rgba(theme.fairy_blue_soft, 92)};"
        "}"
        f"{selector}:focus {{"
        f"border-color: {theme.fairy_blue_glow};"
        "}"
    )


def text_browser_style(
    theme: FairyTheme,
    selector: str = "QTextBrowser",
    *,
    radius: int = 18,
    background: str | None = None,
    border: str | None = None,
    padding: int = 10,
) -> str:
    return (
        f"{selector} {{"
        f"background: {background or rgba(theme.surface, 248)}; color: {theme.text_primary};"
        f"border: 1px solid {border or rgba(theme.divider, 240)}; border-radius: {radius}px; padding: {padding}px;"
        f"font-size: 12px; selection-background-color: {rgba(theme.fairy_blue_soft, 92)};"
        "}"
        f"{selector} a {{ color: {theme.fairy_blue_core}; }}"
    )


def list_widget_style(theme: FairyTheme, selector: str = "QListWidget") -> str:
    hover = rgba(mix(theme.surface_soft, theme.fairy_blue_soft, 0.10), 244)
    selected = rgba(mix(theme.surface_soft, theme.fairy_blue_soft, 0.18), 248)
    return (
        f"{selector} {{"
        "background: transparent; border: none; padding: 6px; outline: none;"
        f"color: {theme.text_primary};"
        "}"
        f"{selector}::item {{"
        "padding: 10px 12px; margin: 4px 0; border-radius: 14px;"
        f"border: 1px solid {rgba(theme.divider, 0)};"
        "}"
        f"{selector}::item:hover {{"
        f"background: {hover}; border: 1px solid {rgba(mix(theme.divider, theme.fairy_blue_soft, 0.20), 235)};"
        "}"
        f"{selector}::item:selected {{"
        f"background: {selected}; border: 1px solid {rgba(mix(theme.divider, theme.fairy_blue_soft, 0.46), 240)};"
        f"color: {theme.text_primary};"
        "}"
    )


def tab_widget_style(theme: FairyTheme) -> str:
    return (
        "QTabWidget::pane {"
        f"border: 1px solid {rgba(theme.divider, 240)}; background: {rgba(theme.surface, 248)}; border-radius: 18px;"
        "}"
        "QTabBar::tab {"
        f"background: {rgba(theme.surface_soft, 232)}; color: {theme.text_secondary};"
        "padding: 8px 14px; border-top-left-radius: 10px; border-top-right-radius: 10px; margin-right: 4px;"
        f"border: 1px solid {rgba(theme.divider, 210)}; border-bottom: none;"
        "}"
        "QTabBar::tab:selected {"
        f"background: {rgba(theme.surface, 252)}; color: {theme.text_primary};"
        f"border-color: {rgba(mix(theme.divider, theme.fairy_blue_soft, 0.38), 238)};"
        "}"
    )

