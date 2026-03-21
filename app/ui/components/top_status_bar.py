from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from app.ui.components.status_chip import StatusChip
from app.ui.i18n import LANG_ZH, localize_mode_label, localize_route_label, normalize_ui_language, tr
from app.ui.theme import apply_soft_shadow, button_style, card_style, resolve_theme, rgba


class TopStatusBar(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._language = LANG_ZH
        theme = resolve_theme(self)
        self.setObjectName("topStatusBar")
        self.setStyleSheet(
            f"""
            {card_style(theme, "QFrame#topStatusBar", radius=20, soft=False)}
            QLabel {{
                color: {theme.text_primary};
            }}
            {button_style(theme)}
            """
        )
        apply_soft_shadow(self, theme, blur=22, y_offset=8, strength=0.9)

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(2)
        self.page_title = QLabel("", self)
        self.page_title.setStyleSheet(f"font-size:20px; font-weight:800; color:{theme.text_primary};")
        self.page_subtitle = QLabel("", self)
        self.page_subtitle.setStyleSheet(f"font-size:11px; color:{theme.text_secondary};")
        self.runtime_meta_label = QLabel("", self)
        self.runtime_meta_label.setStyleSheet(f"font-size:10px; color:{theme.text_secondary};")
        self.runtime_meta_label.setWordWrap(False)
        left.addWidget(self.page_title)
        left.addWidget(self.page_subtitle)
        left.addWidget(self.runtime_meta_label)
        root.addLayout(left, 1)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        chips.setContentsMargins(0, 0, 0, 0)
        self.mode_chip = StatusChip("NORMAL", "active", self)
        self.route_chip = StatusChip("local", "running", self)
        self.pending_chip = StatusChip("0 pending", "pending", self)
        self.jobs_chip = StatusChip("0 jobs", "warning", self)
        self.system_chip = StatusChip("0 active", "inactive", self)
        for chip in (self.mode_chip, self.route_chip, self.pending_chip, self.jobs_chip, self.system_chip):
            chips.addWidget(chip)
        chip_wrap = QWidget(self)
        chip_wrap.setLayout(chips)
        chip_wrap.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        root.addWidget(chip_wrap, 0, Qt.AlignVCenter)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.setContentsMargins(0, 0, 0, 0)
        self.system_button = QPushButton(self)
        self.context_button = QPushButton(self)
        self.settings_button = QPushButton(self)
        self.close_button = QPushButton(self)
        for button in (self.system_button, self.context_button, self.settings_button, self.close_button):
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            button.setMinimumWidth(82)
            actions.addWidget(button)
        actions_wrap = QWidget(self)
        actions_wrap.setLayout(actions)
        actions_wrap.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        root.addWidget(actions_wrap, 0, Qt.AlignRight | Qt.AlignVCenter)

        self.set_ui_language(self._language)

    def set_ui_language(self, language: str) -> None:
        self._language = normalize_ui_language(language)
        self.system_button.setText(tr("system", self._language))
        self.context_button.setText(tr("context", self._language))
        self.settings_button.setText(tr("settings", self._language))
        self.close_button.setText(tr("exit", self._language))
        if not self.page_title.text().strip():
            self.set_page(tr("page_chat_title", self._language), tr("page_chat_subtitle", self._language))

    def set_page(self, title: str, subtitle: str = "") -> None:
        self.page_title.setText(title or tr("page_chat_title", self._language))
        self.page_subtitle.setText(subtitle or "")

    def set_status(
        self,
        *,
        mode_label: str,
        provider_label: str,
        route_label: str,
        backend_label: str,
        fingerprint_label: str,
        pending_count: int,
        running_jobs: int,
        system_count: int = 0,
        system_action_required: int = 0,
        system_critical: int = 0,
        model_label: str = "",
    ) -> None:
        theme = resolve_theme(self)
        localized_mode = localize_mode_label(mode_label or "NORMAL", self._language)
        localized_route = localize_route_label(route_label or "local", self._language)

        self.mode_chip.set_chip(localized_mode, "active" if "GAME" in mode_label or mode_label == "NORMAL" else "info")
        route_tone = "running" if "cloud" in route_label.lower() else "info"
        self.route_chip.set_chip(localized_route, route_tone)
        self.pending_chip.set_chip(
            tr("status_pending", self._language, count=pending_count),
            "pending" if pending_count else "inactive",
        )
        self.jobs_chip.set_chip(
            tr("status_jobs", self._language, count=running_jobs),
            "warning" if running_jobs else "inactive",
        )

        if system_critical:
            self.system_chip.set_chip(tr("status_critical", self._language, count=system_critical), "error")
            self.system_button.setText(f"{tr('system', self._language)} ({system_critical})")
            self.system_button.setStyleSheet(
                f"""
                QPushButton {{
                    color: {theme.error};
                    background: {rgba(theme.surface_soft, 242)};
                    border: 1px solid {rgba(theme.error, 190)};
                    border-radius: 12px;
                    padding: 7px 14px;
                    font-size: 12px;
                    font-weight: 700;
                }}
                QPushButton:hover {{
                    background: {rgba(theme.surface, 248)};
                }}
                """
            )
        elif system_action_required:
            self.system_chip.set_chip(tr("status_action", self._language, count=system_action_required), "pending")
            self.system_button.setText(f"{tr('system', self._language)} ({system_action_required})")
            self.system_button.setStyleSheet(button_style(theme))
        else:
            self.system_chip.set_chip(
                tr("status_active", self._language, count=system_count),
                "warning" if system_count else "inactive",
            )
            self.system_button.setText(tr("system", self._language))
            self.system_button.setStyleSheet(button_style(theme))

        provider_text = provider_label if not model_label else f"{provider_label} / {model_label}"
        meta_parts = [
            f"{tr('meta_provider', self._language)} {self._clip(provider_text, 42)}",
            f"{tr('meta_backend', self._language)} {self._clip(backend_label or 'sqlite', 16)}",
        ]
        if fingerprint_label:
            meta_parts.append(f"{tr('meta_fingerprint', self._language)} {self._clip(fingerprint_label, 18)}")
        self.runtime_meta_label.setText("  ·  ".join(meta_parts))
        self.runtime_meta_label.setToolTip(
            "\n".join(
                part
                for part in (
                    f"{tr('meta_provider', self._language)}: {provider_text or '-'}",
                    f"{tr('meta_backend', self._language)}: {backend_label or '-'}",
                    f"{tr('meta_fingerprint', self._language)}: {fingerprint_label or '-'}",
                )
                if part
            )
        )

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        value = str(text or "").strip()
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 3)].rstrip() + "..."
