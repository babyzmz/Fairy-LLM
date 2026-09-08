from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fairy_core.commanding import (
    CommandLedger,
    CommandRun,
    CommandStatus,
    EventVisibility,
)
from fairy_core.commanding.policy import PermissionProfile, PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import ScopeContract

_DEFAULT_LEASE_DURATION = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class CommandRequest:
    tool_name: str
    actor: str
    scope: ScopeContract
    payload: dict[str, Any]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CommandDispatchResult:
    accepted: bool
    requires_approval: bool
    run: CommandRun | None
    error_code: str | None = None
    reason: str = ""


class CommandBus:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: PolicyEngine,
        ledger: CommandLedger,
        worker_id: str | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._ledger = ledger
        self._worker_id = worker_id

    def submit(
        self,
        request: CommandRequest,
        *,
        profile: PermissionProfile,
        capability_overrides: dict[str, bool],
        sandbox_healthy: bool,
    ) -> CommandDispatchResult:
        definition = self._registry.get(request.tool_name)
        decision = self._policy.evaluate(
            tool_name=request.tool_name,
            profile=profile,
            capability_overrides=capability_overrides,
            approval_granted=False,
            sandbox_healthy=sandbox_healthy,
        )
        if definition is None or (not decision.allowed and not decision.requires_approval):
            return CommandDispatchResult(
                accepted=False,
                requires_approval=False,
                run=None,
                error_code=decision.error_code,
                reason=decision.reason,
            )

        run = self._ledger.create_run(
            command_name=request.tool_name,
            actor=request.actor,
            scope=request.scope,
            input_payload=request.payload,
            risk_level=definition.risk_level,
            idempotency_key=request.idempotency_key,
        )
        if run.status is not CommandStatus.CREATED:
            return CommandDispatchResult(
                accepted=run.status
                not in {
                    CommandStatus.FAILED,
                    CommandStatus.REJECTED,
                    CommandStatus.CANCELLED,
                },
                requires_approval=run.status is CommandStatus.WAITING_APPROVAL,
                run=run,
            )
        if decision.requires_approval:
            run = self._ledger.transition(run.id, CommandStatus.WAITING_APPROVAL)
            return CommandDispatchResult(True, True, run)
        run = self._ledger.transition(run.id, CommandStatus.QUEUED)
        return CommandDispatchResult(True, False, run)

    def decide_approval(self, run_id: UUID, *, approved: bool) -> CommandRun:
        status = CommandStatus.QUEUED if approved else CommandStatus.REJECTED
        return self._ledger.transition(run_id, status)

    def start(
        self,
        run_id: UUID,
        *,
        worker_id: str | None = None,
        lease_until: datetime | None = None,
    ) -> CommandRun:
        return self._ledger.claim(
            run_id,
            worker_id=worker_id or self._worker_id or f"core:{new_id()}",
            lease_until=lease_until or datetime.now(UTC) + _DEFAULT_LEASE_DURATION,
        )

    def complete(
        self,
        run_id: UUID,
        *,
        output: dict[str, Any],
        lease_owner: str | None = None,
        lease_fence: int | None = None,
    ) -> CommandRun:
        return self._ledger.finish(
            run_id,
            status=CommandStatus.SUCCEEDED,
            event_type="command.output",
            visibility=EventVisibility.DEVELOPER,
            message="Command produced output",
            payload=output,
            lease_owner=lease_owner,
            lease_fence=lease_fence,
        )

    def fail(
        self,
        run_id: UUID,
        *,
        error_code: str,
        lease_owner: str | None = None,
        lease_fence: int | None = None,
    ) -> CommandRun:
        return self._ledger.finish(
            run_id,
            status=CommandStatus.FAILED,
            event_type="command.failure",
            visibility=EventVisibility.USER,
            message="Command failed",
            payload={"error_code": error_code},
            lease_owner=lease_owner,
            lease_fence=lease_fence,
        )

    def cancel(
        self,
        run_id: UUID,
        *,
        lease_owner: str | None = None,
        lease_fence: int | None = None,
    ) -> CommandRun:
        return self._ledger.transition(
            run_id,
            CommandStatus.CANCELLED,
            lease_owner=lease_owner,
            lease_fence=lease_fence,
        )
