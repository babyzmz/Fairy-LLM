from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from fairy_core.application.errors import command_rejected
from fairy_core.commanding import CommandLedger, CommandRun, CommandStatus, EventVisibility
from fairy_core.commanding.bus import CommandBus, CommandRequest
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.domain.execution import Approval, ApprovalDecision, Changeset
from fairy_core.domain.models import Conversation, Project, ScopeContract, Task, Version
from fairy_core.persistence.unit_of_work import CoreUnitOfWork
from fairy_core.storage import StateStore


def start_core_command(
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
        raise command_rejected(dispatch, "command was rejected")
    if dispatch.requires_approval:
        from fairy_core.application.errors import ApprovalRequiredError

        raise ApprovalRequiredError(dispatch.reason or "command requires approval")
    if dispatch.run.status is not CommandStatus.QUEUED:
        raise RuntimeError(f"command cannot execute from {dispatch.run.status}")
    return commands.start(dispatch.run.id)


def require_project(state: StateStore, entity_id: UUID) -> Project:
    return _required(state.get_project(entity_id), "project", entity_id)


def require_conversation(state: StateStore, entity_id: UUID) -> Conversation:
    return _required(state.get_conversation(entity_id), "conversation", entity_id)


def require_task(state: StateStore, entity_id: UUID) -> Task:
    return _required(state.get_task(entity_id), "task", entity_id)


def require_version(state: StateStore, entity_id: UUID | None) -> Version:
    if entity_id is None:
        raise ValueError("version_id is required")
    return _required(state.get_version(entity_id), "version", entity_id)


def require_changeset(state: StateStore, entity_id: UUID) -> Changeset:
    return _required(state.get_changeset(entity_id), "changeset", entity_id)


def require_approval(state: StateStore, entity_id: UUID) -> Approval:
    return _required(state.get_approval(entity_id), "approval", entity_id)


def _required(value, label: str, entity_id: UUID):
    if value is None:
        raise KeyError(f"{label} not found: {entity_id}")
    return value


def approve_changeset_by_policy(
    unit_of_work_factory,
    *,
    approval_id: UUID,
    changeset_id: UUID,
) -> None:
    with unit_of_work_factory() as unit_of_work:
        approval = require_approval(unit_of_work.state, approval_id)
        changeset = require_changeset(unit_of_work.state, changeset_id)
        approval.decide(
            decision=ApprovalDecision.APPROVED,
            decided_by="policy:autonomous",
        )
        changeset.record_approval(ApprovalDecision.APPROVED)
        unit_of_work.state.update_approval(
            approval,
            expected_decision=ApprovalDecision.PENDING,
        )
        unit_of_work.state.save_changeset(changeset)
        unit_of_work.commands.append_event(
            run_id=approval.command_run_id,
            event_type="approval.decided",
            visibility=EventVisibility.USER,
            message="Autonomous policy approved Changeset",
            payload={
                "approval_id": str(approval.id),
                "decision": ApprovalDecision.APPROVED.value,
            },
        )
        unit_of_work.commit()


__all__ = [
    "CoreSupportMixin",
    "approve_changeset_by_policy",
    "require_approval",
    "require_changeset",
    "require_conversation",
    "require_project",
    "require_task",
    "require_version",
    "start_core_command",
]


class CoreSupportMixin:
    _require_project = staticmethod(require_project)
    _require_conversation = staticmethod(require_conversation)
    _require_task = staticmethod(require_task)
    _require_version = staticmethod(require_version)
    _require_changeset = staticmethod(require_changeset)
    _require_approval = staticmethod(require_approval)

    def _start_command(
        self,
        unit_of_work: CoreUnitOfWork,
        commands: CommandBus,
        *,
        tool_name: str,
        scope: ScopeContract,
        payload: dict[str, object],
        idempotency_key: str,
    ) -> CommandRun:
        return start_core_command(
            unit_of_work,
            commands,
            execution_policy=self._execution_policy,
            tool_name=tool_name,
            scope=scope,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @contextmanager
    def _transaction(self) -> Iterator[tuple[CoreUnitOfWork, CommandBus]]:
        with self._unit_of_work_factory() as unit_of_work:
            yield unit_of_work, self._command_bus(unit_of_work.commands)

    def _command_bus(self, ledger: CommandLedger) -> CommandBus:
        return CommandBus(registry=self._registry, policy=self._policy, ledger=ledger)
