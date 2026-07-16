from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from fairy_core.commanding.models import (
    CommandRun,
    CommandStatus,
    EventEnvelope,
    EventStreamState,
    EventVisibility,
)
from fairy_core.commanding.registry import RiskLevel
from fairy_core.domain.models import ScopeContract


class CommandLedger(Protocol):
    def create_run(
        self,
        *,
        command_name: str,
        actor: str,
        scope: ScopeContract,
        input_payload: dict[str, Any],
        risk_level: RiskLevel,
        idempotency_key: str,
    ) -> CommandRun: ...

    def get_run(self, run_id: UUID) -> CommandRun | None: ...

    def active_run_for_task(
        self,
        task_id: UUID,
        command_name: str,
    ) -> CommandRun | None: ...

    def transition(
        self,
        run_id: UUID,
        status: CommandStatus,
        *,
        lease_owner: str | None = None,
        lease_fence: int | None = None,
    ) -> CommandRun: ...

    def append_event(
        self,
        *,
        run_id: UUID,
        event_type: str,
        visibility: EventVisibility,
        message: str,
        payload: dict[str, Any],
        lease_owner: str | None = None,
        lease_fence: int | None = None,
    ) -> EventEnvelope: ...

    def finish(
        self,
        run_id: UUID,
        *,
        status: CommandStatus,
        event_type: str,
        visibility: EventVisibility,
        message: str,
        payload: dict[str, Any],
        lease_owner: str | None = None,
        lease_fence: int | None = None,
    ) -> CommandRun: ...

    def events_after(
        self,
        *,
        cursor: int,
        limit: int | None = None,
        allowed_visibilities: set[EventVisibility] | None = None,
    ) -> list[EventEnvelope]: ...

    def stream_state(
        self,
        *,
        allowed_visibilities: set[EventVisibility] | None = None,
    ) -> EventStreamState: ...

    def events_for_run(self, run_id: UUID) -> list[EventEnvelope]: ...

    def current_cursor(self) -> int: ...

    def claim(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        lease_until: datetime,
    ) -> CommandRun: ...

    def claim_next(self, *, worker_id: str, lease_until: datetime) -> CommandRun | None: ...

    def abandon(
        self,
        run_id: UUID,
        *,
        lease_owner: str,
        lease_fence: int,
    ) -> bool: ...

    def renew(
        self,
        run_id: UUID,
        *,
        lease_owner: str,
        lease_fence: int,
        lease_until: datetime,
    ) -> bool: ...

    def close(self) -> None: ...
