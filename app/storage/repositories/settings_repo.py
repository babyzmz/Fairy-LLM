from __future__ import annotations

import json
from typing import Any

from app.storage.db import AppDatabase, get_app_database


class SettingsRepo:
    def __init__(self, db: AppDatabase | None = None) -> None:
        self.db = db or get_app_database()

    def get_json(self, key: str, default: Any = None) -> Any:
        with self.db.connect() as conn:
            row = conn.execute("SELECT value_json FROM app_settings WHERE key = ? LIMIT 1", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(str(row["value_json"]))
        except Exception:
            return default

    def set_json(self, key: str, value: Any) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), self.db.now()),
            )
