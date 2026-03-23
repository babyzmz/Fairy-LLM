from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


DEFAULT_TAURI_BRIDGE_URL = "http://127.0.0.1:8527"


class TauriBridgeClient:
    def __init__(self, *, base_url: str | None = None, timeout_seconds: float = 5.0) -> None:
        configured = str(base_url or os.getenv("FAIRY_TAURI_BRIDGE_URL") or DEFAULT_TAURI_BRIDGE_URL).strip()
        self.base_url = configured.rstrip("/")
        self.timeout_seconds = max(timeout_seconds, 0.5)

    def execute_desktop_action(
        self,
        action: str,
        *,
        payload: dict[str, Any] | None = None,
        cancel_event: Any | None = None,
    ) -> dict[str, Any]:
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            return {
                "status": "error",
                "action": action,
                "message": "Desktop action cancelled before dispatch.",
                "data": {"cancelled": True},
            }

        body = json.dumps({"action": action, "payload": dict(payload or {})}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/bridge/system_action",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
                data = json.loads(raw or "{}")
                if isinstance(data, dict):
                    return data
                return {
                    "status": "error",
                    "action": action,
                    "message": "Desktop bridge returned a non-object response.",
                    "data": {},
                }
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload_data = json.loads(raw or "{}")
            except json.JSONDecodeError:
                payload_data = {}
            message = str(payload_data.get("message") or raw or exc).strip()
            return {
                "status": "error",
                "action": action,
                "message": message or f"Desktop bridge HTTP {exc.code}",
                "data": {"http_status": exc.code},
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "action": action,
                "message": f"Desktop bridge unavailable: {exc}",
                "data": {},
            }
