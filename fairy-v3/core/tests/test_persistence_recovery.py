from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.engine import Engine

from fairy_core.application.core import CoreApplication
from fairy_core.commanding import CommandRun, CommandStatus, EventVisibility
from fairy_core.commanding.bus import CommandBus, CommandRequest
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolRegistry, build_default_registry
from fairy_core.commanding.types import PermissionProfile
from fairy_core.contracts.models import (
    ExecutionTarget,
    MemoryObservationQuery,
    MemoryObserveInput,
    TaskCreate,
)
from fairy_core.domain.errors import MemoryConflictError
from fairy_core.domain.models import (
    OperationMode,
    ProjectResidency,
    ScopeContract,
    WorkspaceType,
)
from fairy_core.memory.application import MemoryApplication
from fairy_core.memory.models import (
    MemoryAuthority,
    MemoryNamespace,
    MemoryObservation,
    MemoryScanResult,
    MemorySensitivity,
    MemorySourceType,
    ObservationStatus,
)
from fairy_core.memory.policy import MemoryPolicy
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner


@dataclass(frozen=True, slots=True)
class _RecoveryStack:
    engine: Engine
    factory: CoreUnitOfWorkFactory
    registry: ToolRegistry
    policy: PolicyEngine
    core: CoreApplication
    memory: MemoryApplication
    task_id: UUID
    conversation_id: UUID
    request: MemoryObserveInput
    scope: ScopeContract


@pytest.fixture
def recovery_stack(tmp_path: Path) -> _RecoveryStack:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    registry = build_default_registry()
    policy = PolicyEngine(registry)
    core = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(tmp_path / "managed"),
        registry=registry,
        policy=policy,
    )
    memory = MemoryApplication(
        unit_of_work_factory=factory,
        registry=registry,
        command_policy=policy,
        memory_policy=MemoryPolicy(),
        scope_resolver=core.scope_for_task,
    )
    project = core.create_project(name="Recovery", residency=ProjectResidency.LOCAL_ONLY)
    conversation = core.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    task = core.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Remember the framework",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="recovery:task",
        )
    )
    request = MemoryObserveInput(
        task_id=task.task.id,
        content="The project uses React Aria.",
        idempotency_key="recovery:observe",
    )
    with factory() as unit_of_work:
        persisted_task = unit_of_work.state.get_task(task.task.id)
        assert persisted_task is not None
        scope = _conversation_draft_scope(core.scope_for_task(unit_of_work.state, persisted_task))
    return _RecoveryStack(
        engine,
        factory,
        registry,
        policy,
        core,
        memory,
        task.task.id,
        conversation.id,
        request,
        scope,
    )


def test_snapshot_failure_rolls_back_task_intent_and_retry_binds_once(
    tmp_path: Path,
) -> None:
    class FailingSnapshotBuilder:
        @staticmethod
        def build(**_values):
            raise RuntimeError("snapshot crash")

    engine = create_sqlite_core_engine(tmp_path / "snapshot-recovery.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    registry = build_default_registry()
    policy = PolicyEngine(registry)
    workspace = FileSystemWorkspaceProvisioner(tmp_path / "snapshot-managed")
    failing = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=workspace,
        registry=registry,
        policy=policy,
        snapshot_builder_factory=lambda _unit_of_work: FailingSnapshotBuilder(),
    )
    project = failing.create_project(
        name="Snapshot recovery",
        residency=ProjectResidency.LOCAL_ONLY,
    )
    conversation = failing.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    request = TaskCreate(
        conversation_id=conversation.id,
        user_request="Recover snapshot binding",
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        execution_target=ExecutionTarget.LOCAL,
        idempotency_key="snapshot:recovery:task",
    )

    with pytest.raises(RuntimeError, match="snapshot crash"):
        failing.create_task(request)

    with factory() as unit_of_work:
        assert (
            unit_of_work.state.find_task_by_idempotency_key(request.idempotency_key)
            is None
        )
        persisted_conversation = unit_of_work.state.get_conversation(conversation.id)
        assert persisted_conversation is not None
        assert persisted_conversation.active_task_id is None
        assert persisted_conversation.active_draft_version_id is None

    recovered = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=workspace,
        registry=registry,
        policy=policy,
    ).create_task(request)

    assert recovered.task.memory_snapshot_id is not None
    assert recovered.scope.memory_snapshot_id == recovered.task.memory_snapshot_id
    with factory() as unit_of_work:
        snapshot = unit_of_work.snapshots.get_for_task(recovered.task.id)
        assert snapshot is not None
        assert snapshot.id == recovered.task.memory_snapshot_id


def test_expired_memory_command_is_reclaimed_without_duplicate_effects(
    recovery_stack: _RecoveryStack,
) -> None:
    running = _start_memory_command(
        recovery_stack,
        lease_until=datetime.now(UTC) + timedelta(milliseconds=5),
    )

    time.sleep(0.02)
    observation = recovery_stack.memory.observe(recovery_stack.request)
    listed = recovery_stack.memory.list_observations(
        MemoryObservationQuery(
            task_id=recovery_stack.task_id,
            namespace=MemoryNamespace.CONVERSATION_DRAFT,
        )
    )
    with recovery_stack.factory() as unit_of_work:
        recovered = unit_of_work.commands.get_run(running.id)
        events = unit_of_work.commands.events_for_run(running.id)

    assert recovered is not None
    assert recovered.status is CommandStatus.SUCCEEDED
    assert recovered.lease_fence == running.lease_fence + 1
    assert listed == (observation,)
    assert [event.event_type for event in events].count("command.reclaimed") == 1
    assert [event.event_type for event in events].count("memory.observation.accepted") == 1


def test_active_memory_lease_cannot_be_stolen(
    recovery_stack: _RecoveryStack,
) -> None:
    running = _start_memory_command(
        recovery_stack,
        lease_until=datetime.now(UTC) + timedelta(seconds=30),
    )

    with pytest.raises(MemoryConflictError, match="active lease"):
        recovery_stack.memory.observe(recovery_stack.request)

    listed = recovery_stack.memory.list_observations(
        MemoryObservationQuery(
            task_id=recovery_stack.task_id,
            namespace=MemoryNamespace.CONVERSATION_DRAFT,
        )
    )
    with recovery_stack.factory() as unit_of_work:
        persisted = unit_of_work.commands.get_run(running.id)
        events = unit_of_work.commands.events_for_run(running.id)
    assert persisted is not None
    assert persisted.status is CommandStatus.RUNNING
    assert persisted.lease_fence == running.lease_fence
    assert listed == ()
    assert "command.reclaimed" not in [event.event_type for event in events]
    assert "memory.observation.accepted" not in [event.event_type for event in events]


def test_result_and_terminal_event_roll_back_together_after_crash(
    recovery_stack: _RecoveryStack,
) -> None:
    running = _start_memory_command(
        recovery_stack,
        lease_until=datetime.now(UTC) + timedelta(seconds=30),
    )
    observation_id = None

    with (
        pytest.raises(RuntimeError, match="result commit crash"),
        recovery_stack.factory() as unit_of_work,
    ):
        source_event = next(
            event
            for event in unit_of_work.commands.events_for_run(running.id)
            if event.event_type == "command.created"
        )
        observation = MemoryObservation.create(
            scope=recovery_stack.scope,
            source_event_id=source_event.id,
            source_cursor=source_event.cursor,
            source_type=MemorySourceType.EXPLICIT_USER_ACTION,
            content=recovery_stack.request.content,
            proposed_namespace=MemoryNamespace.CONVERSATION_DRAFT,
            authority=MemoryAuthority.EXPLICIT_USER,
            confidence=1.0,
            sensitivity=MemorySensitivity.PRIVATE,
            actor="user:core-client",
        ).transition_to(
            ObservationStatus.ACCEPTED,
            scan_result=MemoryScanResult.CLEAN,
        )
        observation_id = observation.id
        unit_of_work.memory.append_observation(
            observation,
            request_fingerprint=hashlib.sha256(b"result-crash").hexdigest(),
        )
        unit_of_work.commands.finish(
            running.id,
            status=CommandStatus.SUCCEEDED,
            event_type="memory.observation.accepted",
            visibility=EventVisibility.USER,
            message="Memory observation accepted",
            payload={"observation_id": str(observation.id)},
            lease_owner=running.lease_owner,
            lease_fence=running.lease_fence,
        )
        raise RuntimeError("result commit crash")

    assert observation_id is not None
    with recovery_stack.factory() as unit_of_work:
        persisted = unit_of_work.commands.get_run(running.id)
        events = unit_of_work.commands.events_for_run(running.id)
        observation = unit_of_work.memory.get_observation(observation_id)
    assert persisted is not None
    assert persisted.status is CommandStatus.RUNNING
    assert observation is None
    assert "memory.observation.accepted" not in [event.event_type for event in events]


def _start_memory_command(
    stack: _RecoveryStack,
    *,
    lease_until: datetime,
) -> CommandRun:
    with stack.factory() as unit_of_work:
        bus = CommandBus(
            registry=stack.registry,
            policy=stack.policy,
            ledger=unit_of_work.commands,
        )
        dispatch = bus.submit(
            CommandRequest(
                tool_name="memory.observe",
                actor="user:core-client",
                scope=stack.scope,
                payload={
                    "task_id": str(stack.task_id),
                    "content": stack.request.content,
                },
                idempotency_key=stack.request.idempotency_key,
            ),
            profile=PermissionProfile.STANDARD,
            capability_overrides={},
            sandbox_healthy=False,
        )
        assert dispatch.run is not None
        running = bus.start(
            dispatch.run.id,
            worker_id="crashed-worker",
            lease_until=lease_until,
        )
        unit_of_work.commit()
        return running


def _conversation_draft_scope(scope: ScopeContract) -> ScopeContract:
    return ScopeContract.create(
        workspace_type=scope.workspace_type,
        project_id=scope.project_id,
        conversation_id=scope.conversation_id,
        task_id=scope.task_id,
        operation_mode=scope.operation_mode,
        base_version_id=scope.base_version_id,
        target_version_id=scope.target_version_id,
        project_root=scope.project_root,
        allowed_write_paths=scope.allowed_write_paths,
        forbidden_write_paths=scope.forbidden_write_paths,
        execution_target=scope.execution_target,
        network_policy=scope.network_policy,
        memory_read_scope=scope.memory_read_scope,
        memory_write_scope=(MemoryNamespace.CONVERSATION_DRAFT.value,),
        memory_snapshot_id=scope.memory_snapshot_id,
        memory_snapshot_hash=scope.memory_snapshot_hash,
    )
