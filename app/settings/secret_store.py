from __future__ import annotations

import json
from pathlib import Path

from app.config import LOCAL_SECRETS_FILE


class SecretStore:
    def __init__(self, path: Path = LOCAL_SECRETS_FILE) -> None:
        self.path = path

    def _normalize_secret(self, value: str) -> str:
        if not value:
            return ""
        # API keys should never contain embedded newlines or tabs.
        return "".join(str(value).split()).strip()

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {str(key): str(value) for key, value in data.items() if value is not None}

    def _save(self, payload: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, ref: str) -> str:
        if not ref:
            return ""
        return self._normalize_secret(self._load().get(ref, ""))

    def set(self, ref: str, value: str) -> None:
        if not ref:
            return
        payload = self._load()
        payload[ref] = self._normalize_secret(value)
        self._save(payload)

    def clear(self, ref: str) -> None:
        if not ref:
            return
        payload = self._load()
        payload.pop(ref, None)
        self._save(payload)

    def mask(self, ref: str) -> str:
        value = self.get(ref)
        if not value:
            return ""
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}{'*' * max(4, len(value) - 8)}{value[-4:]}"
