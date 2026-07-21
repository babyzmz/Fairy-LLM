from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

from fairy_core.commanding.models import EventVisibility
from fairy_core.domain.ids import new_id
from fairy_core.memory.models import MemoryTombstone
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory

_MAINTENANCE_INTERVAL = timedelta(days=1)
_PAYLOAD_PURGE_GRACE = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class MemoryRetentionResult:
    expired: int
    purged_observations: int
    purged_claims: int
    ran_at: datetime

    @property
    def purged(self) -> int:
        return self.purged_observations + self.purged_claims


class MemoryRetentionCoordinator:
    """Applies durable retention without creating a resident worker thread."""

    def __init__(
        self,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        *,
        clock=lambda: datetime.now(UTC),
        maintenance_interval: timedelta = _MAINTENANCE_INTERVAL,
        purge_grace: timedelta = _PAYLOAD_PURGE_GRACE,
    ) -> None:
        if maintenance_interval <= timedelta(0):
            raise ValueError("memory maintenance_interval must be positive")
        if purge_grace < timedelta(0):
            raise ValueError("memory purge_grace cannot be negative")
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._maintenance_interval = maintenance_interval
        self._purge_grace = purge_grace
        self._last_run_at: datetime | None = None
        self._lock = Lock()

    def run_if_due(self, *, force: bool = False) -> MemoryRetentionResult | None:
        with self._lock:
            now = self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("memory retention clock must be timezone-aware")
            now = now.astimezone(UTC)
            if (
                not force
                and self._last_run_at is not None
                and now - self._last_run_at < self._maintenance_interval
            ):
                return None
            result = self._run(now)
            self._last_run_at = now
            return result

    def _run(self, now: datetime) -> MemoryRetentionResult:
        with self._unit_of_work_factory() as unit_of_work:
            settings = unit_of_work.memory_settings.get()
            cutoff = now - timedelta(days=settings.retention_days)
            candidates = unit_of_work.memory_retention.retention_candidates(before=cutoff)
            for candidate in candidates:
                tombstone = MemoryTombstone(
                    id=new_id(),
                    target_kind=candidate.target_kind,
                    target_id=candidate.target_id,
                    reason=f"Memory retention period of {settings.retention_days} days expired",
                    actor="core:retention",
                    source_event_id=candidate.source_event_id,
                    created_at=now,
                )
                unit_of_work.memory.forget(
                    tombstone,
                    request_fingerprint=_retention_fingerprint(
                        candidate.target_kind.value, candidate.target_id
                    ),
                )
                unit_of_work.commands.append_domain_event(
                    event_type="memory.retention.expired",
                    visibility=EventVisibility.INTERNAL,
                    message="Memory retention expired content",
                    payload={
                        "target_kind": candidate.target_kind.value,
                        "target_id": str(candidate.target_id),
                        "retention_days": settings.retention_days,
                    },
                    actor="core:retention",
                    project_id=candidate.project_id,
                    conversation_id=candidate.conversation_id,
                    task_id=candidate.task_id,
                    version_id=candidate.version_id,
                )
            purged = unit_of_work.memory_retention.purge_forgotten_payloads(
                before=now - self._purge_grace
            )
            unit_of_work.commit()
        return MemoryRetentionResult(
            expired=len(candidates),
            purged_observations=purged.observations,
            purged_claims=purged.claims,
            ran_at=now,
        )


def _retention_fingerprint(target_kind: str, target_id: object) -> str:
    return hashlib.sha256(f"memory-retention:{target_kind}:{target_id}".encode()).hexdigest()


__all__ = ["MemoryRetentionCoordinator", "MemoryRetentionResult"]
