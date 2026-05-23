from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.config import system_config


@dataclass(slots=True)
class FairyBootResult:
    first_launch_today: bool
    state: str
    model_online: bool | None
    network_online: bool
    status_text: str


class FairyInitializer:
    """Handles Fairy boot state, daily welcome speech gating, and capability snapshot."""

    def __init__(self, tts=None, state_file=None) -> None:
        self.tts = tts
        self.state_file = state_file or system_config.state_file
        self._welcome_pending = False

    def boot(self, runtime_snapshot: dict[str, Any] | None = None) -> FairyBootResult:
        state = self._load_state()
        today = date.today().isoformat()
        first_launch_today = state.get("last_boot_date") != today
        if first_launch_today:
            self._save_state({"last_boot_date": today})
        self._welcome_pending = first_launch_today

        result = self.build_boot_result(runtime_snapshot)
        return FairyBootResult(first_launch_today=first_launch_today, **result)

    def build_boot_result(self, runtime_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        snapshot = dict(runtime_snapshot or {})
        model_online = snapshot.get("model_online")
        network_online = self.check_network()

        if model_online is True:
            state = "idle"
            status_text = system_config.startup_status_lines[-1]
        elif model_online is False:
            state = "error"
            status_text = "核心模块异常。"
        else:
            state = "warming_up"
            status_text = system_config.startup_status_lines[1]

        return {
            "state": state,
            "model_online": model_online if isinstance(model_online, bool) else None,
            "network_online": network_online,
            "status_text": status_text,
        }

    def check_network(self) -> bool:
        try:
            with socket.create_connection(("1.1.1.1", 53), timeout=1.0):
                return True
        except OSError:
            return False

    def has_pending_welcome(self) -> bool:
        return self._welcome_pending

    def is_first_launch_today(self) -> bool:
        return self._welcome_pending

    def mark_welcome_played(self) -> None:
        self._welcome_pending = False

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
