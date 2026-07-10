from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fairy_core.commanding.ledger import CommandRun, CommandStatus, SqliteCommandLedger
from fairy_core.commanding.policy import PermissionProfile, PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.domain.models import ScopeContract


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
        ledger: SqliteCommandLedger,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._ledger = ledger

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
