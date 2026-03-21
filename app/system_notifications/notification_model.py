from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class SystemNotification:
    id: str
    canonical_key: str
    type: str
    level: str
    title: str
    message: str
    created_at: str
    updated_at: str
    expires_at: str = ""
    related_entity_type: str = ""
    related_entity_id: str = ""
    is_dismissed: bool = False
    is_completed: bool = False
    snooze_until: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "SystemNotification":
        try:
            metadata = json.loads(str(row.get("metadata_json") or "{}"))
        except Exception:
            metadata = {}
        return cls(
            id=str(row.get("id", "") or ""),
            canonical_key=str(row.get("canonical_key", "") or ""),
            type=str(row.get("type", "") or ""),
            level=str(row.get("level", "") or ""),
            title=str(row.get("title", "") or ""),
            message=str(row.get("message", "") or ""),
            created_at=str(row.get("created_at", "") or ""),
            updated_at=str(row.get("updated_at", "") or ""),
            expires_at=str(row.get("expires_at", "") or ""),
            related_entity_type=str(row.get("related_entity_type", "") or ""),
            related_entity_id=str(row.get("related_entity_id", "") or ""),
            is_dismissed=bool(int(row.get("is_dismissed", 0) or 0)),
            is_completed=bool(int(row.get("is_completed", 0) or 0)),
            snooze_until=str(row.get("snooze_until", "") or ""),
            metadata=metadata,
        )
