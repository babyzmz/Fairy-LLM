from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MemorySettings:
    enabled: bool
    retention_days: int
    export_to_obsidian: bool
    sync_normalized_content: bool
    revision: int
    updated_at: datetime

    def __post_init__(self) -> None:
        if not 1 <= self.retention_days <= 3_650:
            raise ValueError("memory retention_days must be between 1 and 3650")
        if self.revision < 0:
            raise ValueError("memory settings revision cannot be negative")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("memory settings updated_at must be timezone-aware")
        object.__setattr__(self, "updated_at", self.updated_at.astimezone(UTC))

    @classmethod
    def defaults(cls, *, now: datetime | None = None) -> MemorySettings:
        return cls(
            enabled=True,
            retention_days=365,
            export_to_obsidian=False,
            sync_normalized_content=False,
            revision=0,
            updated_at=now or datetime.now(UTC),
        )


class MemorySettingsRepository(Protocol):
    def get(self) -> MemorySettings: ...

    def update(
        self,
        *,
        enabled: bool,
        retention_days: int,
        export_to_obsidian: bool,
        sync_normalized_content: bool,
        expected_revision: int,
        idempotency_key: str,
    ) -> MemorySettings: ...


__all__ = ["MemorySettings", "MemorySettingsRepository"]
