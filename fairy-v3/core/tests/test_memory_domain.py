from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fairy_core.contracts.models import ErrorCode
from fairy_core.domain.errors import (
    InvalidTransitionError,
    MemoryConflictError,
    MemoryForgottenError,
    MemoryInjectionBlockedError,
    MemoryProjectionStaleError,
    MemoryScopeViolationError,
    MemorySecretBlockedError,
    MemorySnapshotTooLargeError,
)
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import OperationMode, ScopeContract, WorkspaceType
from fairy_core.memory.models import (
    CLAIM_TRANSITIONS,
    ClaimStatus,
    MemoryAuthority,
    MemoryClaim,
    MemoryClaimRevision,
    MemoryNamespace,
    MemoryObservation,
    MemorySensitivity,
    MemoryTargetKind,
    MemoryTombstone,
    ObservationStatus,
)


def _scope(tmp_path) -> ScopeContract:
    project_id = new_id()
    conversation_id = new_id()
    task_id = new_id()
    version_id = new_id()
    return ScopeContract.create(
        workspace_type=WorkspaceType.PROJECT_CHAT,
        project_id=project_id,
        conversation_id=conversation_id,
        task_id=task_id,
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=version_id,
        target_version_id=version_id,
        project_root=tmp_path / "version",
        allowed_write_paths=(tmp_path / "version",),
        forbidden_write_paths=(),
        execution_target="local",
        network_policy="off",
        memory_read_scope=("project_canonical", "conversation_draft"),
        memory_write_scope=("project_canonical", "conversation_draft"),
    )


def _revision(claim: MemoryClaim, revision: int) -> MemoryClaimRevision:
    return MemoryClaimRevision.create(
        claim_id=claim.id,
        revision=revision,
        value={"style": "compact", "weights": [1, 2]},
        normalized_text="style compact weights 1 2",
        source_observation_ids=(new_id(),),
        source_event_ids=(new_id(),),
        authority=MemoryAuthority.EXPLICIT_USER,
        confidence=1.0,
        actor="user:local",
        supersedes_revision=revision - 1 if revision > 1 else None,
    )


def claim_in_status(status: ClaimStatus) -> MemoryClaim:
    claim = MemoryClaim.create(
        namespace=MemoryNamespace.USER_PROFILE,
        subject="user",
        predicate="interface_density",
    )
    if status is not ClaimStatus.CANDIDATE:
        claim.transition_to(status)
    return claim


def test_memory_enums_are_stable() -> None:
    assert tuple(MemoryNamespace) == (
        MemoryNamespace.PROJECT_CANONICAL,
        MemoryNamespace.CONVERSATION_DRAFT,
        MemoryNamespace.USER_PROFILE,
        MemoryNamespace.DEVICE_LOCAL,
        MemoryNamespace.TASK_EPISODE,
    )
    assert set(ClaimStatus) == {
        ClaimStatus.CANDIDATE,
        ClaimStatus.ACTIVE,
        ClaimStatus.CONFLICTED,
        ClaimStatus.SUPERSEDED,
        ClaimStatus.EXPIRED,
        ClaimStatus.REJECTED,
        ClaimStatus.FORGOTTEN,
    }
    assert set(ObservationStatus) == {
        ObservationStatus.PENDING,
        ObservationStatus.ACCEPTED,
        ObservationStatus.REJECTED,
        ObservationStatus.PROMOTED,
        ObservationStatus.FORGOTTEN,
    }


@given(st.sampled_from(tuple(ClaimStatus)), st.sampled_from(tuple(ClaimStatus)))
def test_claim_state_machine_matches_declared_transitions(
    current: ClaimStatus,
    target: ClaimStatus,
) -> None:
    claim = claim_in_status(current)
    if target in CLAIM_TRANSITIONS[current]:
        claim.transition_to(target)
        assert claim.status is target
    else:
        with pytest.raises(InvalidTransitionError):
            claim.transition_to(target)


def test_observation_binds_core_scope_and_immutable_provenance(tmp_path) -> None:
    scope = _scope(tmp_path)
    event_id = new_id()
    observation = MemoryObservation.create(
        scope=scope,
        source_event_id=event_id,
        source_cursor=11,
        source_type="user_message",
        content="The project uses React Aria.",
        proposed_namespace=MemoryNamespace.PROJECT_CANONICAL,
        authority=MemoryAuthority.EXPLICIT_USER,
        confidence=0.95,
        sensitivity=MemorySensitivity.PRIVATE,
        actor="user:local",
    )

    assert observation.project_id == scope.project_id
    assert observation.conversation_id == scope.conversation_id
    assert observation.task_id == scope.task_id
    assert observation.version_id == scope.target_version_id
    assert observation.scope_digest == scope.scope_digest
    assert observation.source_event_id == event_id
    assert len(observation.content_hash) == 64
    with pytest.raises(FrozenInstanceError):
        observation.actor = "model"  # type: ignore[misc]


def test_claim_revisions_are_monotonic_and_keep_immutable_evidence() -> None:
    claim = MemoryClaim.create(
        namespace=MemoryNamespace.USER_PROFILE,
        subject="user",
        predicate="interface_density",
    )
    first = _revision(claim, 1)
    claim.record_revision(first)

    with pytest.raises(MemoryConflictError):
        claim.record_revision(_revision(claim, 3))

    second = _revision(claim, 2)
    claim.record_revision(second)

    assert claim.current_revision == 2
    assert second.supersedes_revision == 1
    assert second.source_observation_ids
    assert second.source_event_ids
    value = second.value
    assert isinstance(value, dict)
    value["style"] = "spacious"
    assert second.value["style"] == "compact"
    with pytest.raises(FrozenInstanceError):
        second.actor = "model"  # type: ignore[misc]


def test_forgotten_claim_rejects_new_revisions() -> None:
    claim = claim_in_status(ClaimStatus.FORGOTTEN)

    with pytest.raises(MemoryForgottenError):
        claim.record_revision(_revision(claim, 1))


def test_revision_rejects_invalid_valid_time() -> None:
    claim = claim_in_status(ClaimStatus.CANDIDATE)
    now = datetime.now(UTC)

    with pytest.raises(ValueError, match="valid_to"):
        MemoryClaimRevision.create(
            claim_id=claim.id,
            revision=1,
            value=True,
            normalized_text="true",
            source_observation_ids=(new_id(),),
            source_event_ids=(new_id(),),
            authority=MemoryAuthority.DETERMINISTIC_CORE,
            confidence=1.0,
            actor="core",
            valid_from=now,
            valid_to=now - timedelta(seconds=1),
        )


def test_claim_namespace_requires_its_owner_scope() -> None:
    with pytest.raises(ValueError, match="project_id"):
        MemoryClaim.create(
            namespace=MemoryNamespace.PROJECT_CANONICAL,
            subject="project",
            predicate="framework",
        )
    with pytest.raises(ValueError, match="conversation_id"):
        MemoryClaim.create(
            namespace=MemoryNamespace.CONVERSATION_DRAFT,
            subject="draft",
            predicate="plan",
        )
    with pytest.raises(ValueError, match="device_id"):
        MemoryClaim.create(
            namespace=MemoryNamespace.DEVICE_LOCAL,
            subject="device",
            predicate="sandbox_health",
        )
    with pytest.raises(ValueError, match="task_id"):
        MemoryClaim.create(
            namespace=MemoryNamespace.TASK_EPISODE,
            subject="task",
            predicate="outcome",
        )
    with pytest.raises(ValueError, match="user_profile"):
        MemoryClaim.create(
            namespace=MemoryNamespace.USER_PROFILE,
            subject="user",
            predicate="preference",
            version_id=new_id(),
        )


def test_tombstone_is_immutable_and_attributable() -> None:
    source_event_id = new_id()
    tombstone = MemoryTombstone.create(
        target_kind=MemoryTargetKind.CLAIM,
        target_id=new_id(),
        reason="User requested deletion",
        actor="user:local",
        source_event_id=source_event_id,
    )

    assert tombstone.source_event_id == source_event_id
    assert tombstone.created_at.tzinfo is UTC
    with pytest.raises(FrozenInstanceError):
        tombstone.reason = "changed"  # type: ignore[misc]


def test_memory_error_codes_are_stable_in_domain_and_contracts() -> None:
    errors = (
        MemoryScopeViolationError,
        MemoryConflictError,
        MemoryInjectionBlockedError,
        MemorySecretBlockedError,
        MemoryProjectionStaleError,
        MemorySnapshotTooLargeError,
        MemoryForgottenError,
    )
    assert {error.code for error in errors} == {
        "MEMORY_SCOPE_VIOLATION",
        "MEMORY_CONFLICT",
        "MEMORY_INJECTION_BLOCKED",
        "MEMORY_SECRET_BLOCKED",
        "MEMORY_PROJECTION_STALE",
        "MEMORY_SNAPSHOT_TOO_LARGE",
        "MEMORY_FORGOTTEN",
    }
    assert {code.value for code in ErrorCode if code.name.startswith("MEMORY_")} == {
        error.code for error in errors
    }
