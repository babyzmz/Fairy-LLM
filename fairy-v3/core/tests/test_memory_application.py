from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import update

from fairy_core.application.core import CoreApplication
from fairy_core.application.errors import ApprovalRequiredError
from fairy_core.commanding import EventVisibility, SqlAlchemyCommandLedger
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import (
    ApprovalPolicy,
    RiskLevel,
    SideEffect,
    build_default_registry,
)
from fairy_core.contracts.models import (
    ExecutionTarget,
    MemoryClaimGetInput,
    MemoryClaimPromoteInput,
    MemoryClaimQuery,
    MemoryClaimResolveInput,
    MemoryClaimSupersedeInput,
    MemoryForgetInput,
    MemoryObservationQuery,
    MemoryObserveInput,
    TaskCreate,
)
from fairy_core.domain.errors import MemoryScopeViolationError
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import (
    OperationMode,
    ProjectResidency,
    WorkspaceType,
)
from fairy_core.memory.application import MemoryApplication
from fairy_core.memory.models import ClaimStatus, MemoryNamespace
from fairy_core.memory.policy import MemoryPolicy
from fairy_core.memory.schema import memory_claims
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner


def _applications(tmp_path: Path):
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    registry = build_default_registry()
    core = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(tmp_path / "managed"),
        registry=registry,
        policy=PolicyEngine(registry),
    )
    memory = MemoryApplication(
        unit_of_work_factory=factory,
        registry=registry,
        command_policy=PolicyEngine(registry),
        memory_policy=MemoryPolicy(),
        scope_resolver=core.scope_for_task,
    )
    return engine, core, memory


def _task(core: CoreApplication, *, suffix: str = "one"):
    project = core.create_project(
        name=f"Memory {suffix}",
        residency=ProjectResidency.LOCAL_ONLY,
    )
    conversation = core.create_conversation(
        project_id=project.project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
    )
    return core.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Remember the framework",
            operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key=f"task:{suffix}",
        )
    )


def test_memory_tools_have_exact_command_policy_metadata() -> None:
    registry = build_default_registry()
    definitions = {
        definition.name: definition
        for definition in registry.definitions()
        if definition.name.startswith("memory.")
    }

    assert set(definitions) == {
        "memory.observe",
        "memory.claim.promote",
        "memory.claim.supersede",
        "memory.claim.resolve_conflict",
        "memory.forget",
    }
    assert definitions["memory.observe"].side_effect is SideEffect.WRITE
    assert definitions["memory.observe"].risk_level is RiskLevel.LOW
    assert definitions["memory.observe"].approval_policy is ApprovalPolicy.NEVER
    assert all(definition.idempotent for definition in definitions.values())
    assert all(
        definitions[name].approval_policy is ApprovalPolicy.ALWAYS
        for name in definitions
        if name != "memory.observe"
    )


def test_observe_promote_supersede_resolve_and_forget_are_command_driven(
    tmp_path: Path,
) -> None:
    engine, core, memory = _applications(tmp_path)
    task = _task(core)
    observe_request = MemoryObserveInput(
        task_id=task.task.id,
        content="The project uses React Aria.",
        idempotency_key="memory:observe",
    )

    observation = memory.observe(observe_request)
    replay = memory.observe(observe_request)
    listed_observations = memory.list_observations(
        MemoryObservationQuery(
            task_id=task.task.id,
            namespace=MemoryNamespace.CONVERSATION_DRAFT,
        )
    )

    assert replay.id == observation.id
    assert observation.project_id == task.task.project_id
    assert observation.conversation_id == task.task.conversation_id
    assert observation.task_id == task.task.id
    assert listed_observations == (observation,)

    promote_request = MemoryClaimPromoteInput(
        task_id=task.task.id,
        observation_id=observation.id,
        subject="project",
        predicate="accessibility_framework",
        value="React Aria",
        normalized_text="react aria",
        user_confirmed=True,
        idempotency_key="memory:promote",
    )
    promoted = memory.promote_claim(promote_request)
    promoted_replay = memory.promote_claim(promote_request)

    assert promoted.claim.status is ClaimStatus.ACTIVE
    assert promoted.current_revision.revision == 1
    assert promoted_replay.claim.id == promoted.claim.id

    superseded = memory.supersede_claim(
        MemoryClaimSupersedeInput(
            task_id=task.task.id,
            claim_id=promoted.claim.id,
            expected_revision=1,
            source_observation_ids=(observation.id,),
            value="React Aria 4",
            normalized_text="react aria 4",
            user_confirmed=True,
            idempotency_key="memory:supersede",
        )
    )
    assert superseded.current_revision.revision == 2
    assert superseded.current_revision.supersedes_revision == 1

    conflict_set_id = new_id()
    with engine.begin() as connection:
        connection.execute(
            update(memory_claims)
            .where(
                memory_claims.c.tenant_id == "local",
                memory_claims.c.id == str(promoted.claim.id),
            )
            .values(
                status=ClaimStatus.CONFLICTED.value,
                conflict_set_id=str(conflict_set_id),
            )
        )
    resolved = memory.resolve_conflict(
        MemoryClaimResolveInput(
            task_id=task.task.id,
            claim_id=promoted.claim.id,
            expected_revision=2,
            source_observation_ids=(observation.id,),
            resolved_claim_ids=(promoted.claim.id,),
            value="React Aria 4.1",
            normalized_text="react aria 4.1",
            user_confirmed=True,
            idempotency_key="memory:resolve",
        )
    )
    assert resolved.claim.status is ClaimStatus.ACTIVE
    assert resolved.current_revision.resolved_claim_ids == (promoted.claim.id,)

    inspected = memory.get_claim(
        MemoryClaimGetInput(task_id=task.task.id, claim_id=promoted.claim.id)
    )
    listed_claims = memory.list_claims(
        MemoryClaimQuery(
            task_id=task.task.id,
            namespace=MemoryNamespace.PROJECT_CANONICAL,
        )
    )
    assert inspected.current_revision.revision == 3
    assert [item.claim.id for item in listed_claims] == [promoted.claim.id]

    forgotten = memory.forget(
        MemoryForgetInput(
            task_id=task.task.id,
            target_kind="claim",
            target_id=promoted.claim.id,
            reason="Superseded by an explicit project decision",
            user_confirmed=True,
            idempotency_key="memory:forget",
        )
    )
    assert forgotten.target_id == promoted.claim.id
    assert (
        memory.list_claims(
            MemoryClaimQuery(
                task_id=task.task.id,
                namespace=MemoryNamespace.PROJECT_CANONICAL,
            )
        )
        == ()
    )

    events = SqlAlchemyCommandLedger(engine, tenant_id="local").events_after(
        cursor=0,
        allowed_visibilities={EventVisibility.USER, EventVisibility.DEVELOPER},
    )
    memory_events = [event for event in events if event.event_type.startswith("memory.")]
    assert [event.event_type for event in memory_events] == [
        "memory.observation.accepted",
        "memory.claim.promoted",
        "memory.claim.superseded",
        "memory.claim.resolved",
        "memory.claim.forgotten",
    ]
    assert all("content" not in event.payload and "value" not in event.payload for event in events)


def test_canonical_promotion_requires_confirmation(tmp_path: Path) -> None:
    _engine, core, memory = _applications(tmp_path)
    task = _task(core)
    observation = memory.observe(
        MemoryObserveInput(
            task_id=task.task.id,
            content="The project uses React.",
            idempotency_key="memory:observe:approval",
        )
    )

    with pytest.raises(ApprovalRequiredError):
        memory.promote_claim(
            MemoryClaimPromoteInput(
                task_id=task.task.id,
                observation_id=observation.id,
                subject="project",
                predicate="framework",
                value="React",
                normalized_text="react",
                user_confirmed=False,
                idempotency_key="memory:promote:approval",
            )
        )


def test_observation_cannot_cross_task_scope(tmp_path: Path) -> None:
    _engine, core, memory = _applications(tmp_path)
    first = _task(core, suffix="first")
    second = _task(core, suffix="second")
    observation = memory.observe(
        MemoryObserveInput(
            task_id=first.task.id,
            content="First project fact",
            idempotency_key="memory:observe:first",
        )
    )

    with pytest.raises(MemoryScopeViolationError):
        memory.promote_claim(
            MemoryClaimPromoteInput(
                task_id=second.task.id,
                observation_id=observation.id,
                subject="project",
                predicate="fact",
                value="First project fact",
                normalized_text="first project fact",
                user_confirmed=True,
                idempotency_key="memory:promote:wrong-task",
            )
        )
