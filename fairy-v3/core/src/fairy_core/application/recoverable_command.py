from __future__ import annotations

from datetime import UTC, datetime

from fairy_core.application.errors import ApprovalRequiredError
from fairy_core.commanding import CommandRun, CommandStatus
from fairy_core.commanding.bus import CommandBus, CommandRequest
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.domain.models import ScopeContract
from fairy_core.persistence.unit_of_work import CoreUnitOfWork


def start_recoverable_core_command(
    unit_of_work: CoreUnitOfWork,
    commands: CommandBus,
    *,
    execution_policy: ExecutionPolicyResolver,
    tool_name: str,
    scope: ScopeContract,
    payload: dict[str, object],
    idempotency_key: str,
) -> CommandRun:
    policy = execution_policy.resolve(
        unit_of_work.execution_settings,
        execution_target=scope.execution_target,
    )
    dispatch = commands.submit(
        CommandRequest(
            tool_name=tool_name,
            actor="core",
            scope=scope,
            payload=payload,
            idempotency_key=idempotency_key,
        ),
        profile=policy.profile,
        capability_overrides=dict(policy.capability_overrides),
        sandbox_healthy=policy.sandbox_healthy,
    )
    if not dispatch.accepted or dispatch.run is None:
        raise RuntimeError(dispatch.error_code or "recoverable command was rejected")
    if dispatch.requires_approval:
        raise ApprovalRequiredError(dispatch.reason or "command requires approval")
    run = dispatch.run
    if run.status is CommandStatus.SUCCEEDED:
        return run
    if run.status is CommandStatus.INTERRUPTED:
        run = unit_of_work.commands.transition(run.id, CommandStatus.QUEUED)
    if run.status is CommandStatus.QUEUED:
        return commands.start(run.id)
    if (
        run.status is CommandStatus.RUNNING
        and run.lease_until is not None
        and run.lease_until <= datetime.now(UTC)
    ):
        return commands.start(run.id)
    if run.status is CommandStatus.RUNNING:
        raise RuntimeError("recoverable command still has an active worker lease")
    raise RuntimeError(f"recoverable command cannot execute from {run.status}")


__all__ = ["start_recoverable_core_command"]
