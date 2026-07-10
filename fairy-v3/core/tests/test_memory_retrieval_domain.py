from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from fairy_core.domain.ids import new_id
from fairy_core.memory.models import MemoryAuthority, MemoryNamespace
from fairy_core.memory.retrieval_models import (
    MemoryProjectionHealth,
    MemorySearchDocument,
    MemorySearchHit,
    MemorySelectionReason,
    MemorySnapshot,
    MemorySnapshotItem,
    MemorySnapshotStatus,
    MemorySourceKind,
    ProjectionState,
)


def _snapshot_item(*, ordinal: int = 0, rendered_text: str = "React is required."):
    return MemorySnapshotItem.create(
        ordinal=ordinal,
        source_kind=MemorySourceKind.CLAIM_REVISION,
        source_id=new_id(),
        source_revision=2,
        namespace=MemoryNamespace.PROJECT_CANONICAL,
        selection_reason=MemorySelectionReason.EXACT_CANONICAL,
        authority=MemoryAuthority.ACCEPTED_VERSION,
        score_components={"authority": 1.0, "exact": 1.0, "lexical": 0.75},
        rendered_text=rendered_text,
        token_count=len(rendered_text.encode("utf-8")),
    )


def _snapshot(*, item: MemorySnapshotItem, status=MemorySnapshotStatus.READY):
    project_id = new_id()
    conversation_id = new_id()
    task_id = new_id()
    version_id = new_id()
    return MemorySnapshot.create(
        project_id=project_id,
        conversation_id=conversation_id,
        task_id=task_id,
        base_version_id=version_id,
        target_version_id=version_id,
        policy_version="hermes-lexical-v1",
        source_watermark_cursor=41,
        projection_generation=1,
        projection_watermark_cursor=41,
        projection_state=ProjectionState.READY,
        status=status,
        degraded_reason=None,
        items=(item,),
    )


def test_retrieval_enums_are_stable() -> None:
    assert tuple(MemorySourceKind) == (
        MemorySourceKind.CLAIM_REVISION,
        MemorySourceKind.OBSERVATION,
        MemorySourceKind.DOMAIN_EVENT,
    )
    assert set(MemorySnapshotStatus) == {
        MemorySnapshotStatus.READY,
        MemorySnapshotStatus.DEGRADED,
    }
    assert set(ProjectionState) == {
        ProjectionState.READY,
        ProjectionState.STALE,
        ProjectionState.UNAVAILABLE,
        ProjectionState.FAILED,
    }


def test_snapshot_item_recomputes_hash_and_freezes_scores() -> None:
    item = _snapshot_item()

    assert len(item.rendered_text_hash) == 64
    assert isinstance(item.score_components, MappingProxyType)
    with pytest.raises(TypeError):
        item.score_components["exact"] = 0.0  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        item.rendered_text = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.01, 1.01])
def test_snapshot_item_rejects_invalid_scores(score: float) -> None:
    with pytest.raises(ValueError, match="score"):
        MemorySnapshotItem.create(
            ordinal=0,
            source_kind=MemorySourceKind.OBSERVATION,
            source_id=new_id(),
            source_revision=None,
            namespace=MemoryNamespace.CONVERSATION_DRAFT,
            selection_reason=MemorySelectionReason.LEXICAL_HISTORY,
            authority=MemoryAuthority.EXPLICIT_USER,
            score_components={"lexical": score},
            rendered_text="source",
            token_count=6,
        )


def test_snapshot_hash_is_reproducible_and_sensitive_to_ordered_content() -> None:
    item = _snapshot_item()
    first = _snapshot(item=item)
    second = MemorySnapshot.restore(
        id=new_id(),
        project_id=first.project_id,
        conversation_id=first.conversation_id,
        task_id=first.task_id,
        base_version_id=first.base_version_id,
        target_version_id=first.target_version_id,
        snapshot_version=first.snapshot_version,
        policy_version=first.policy_version,
        source_watermark_cursor=first.source_watermark_cursor,
        projection_generation=first.projection_generation,
        projection_watermark_cursor=first.projection_watermark_cursor,
        projection_state=first.projection_state,
        status=first.status,
        degraded_reason=first.degraded_reason,
        content_hash=first.content_hash,
        token_count=first.token_count,
        items=first.items,
        created_at=datetime.now(UTC),
    )
    changed = MemorySnapshot.create(
        project_id=first.project_id,
        conversation_id=first.conversation_id,
        task_id=first.task_id,
        base_version_id=first.base_version_id,
        target_version_id=first.target_version_id,
        policy_version=first.policy_version,
        source_watermark_cursor=first.source_watermark_cursor,
        projection_generation=first.projection_generation,
        projection_watermark_cursor=first.projection_watermark_cursor,
        projection_state=first.projection_state,
        status=first.status,
        degraded_reason=first.degraded_reason,
        items=(_snapshot_item(rendered_text="Vue is required."),),
    )

    assert first.content_hash == second.content_hash
    assert first.id != second.id
    assert first.content_hash != changed.content_hash
    assert first.token_count == item.token_count


def test_snapshot_restore_rejects_tampered_hash_and_non_contiguous_items() -> None:
    first = _snapshot_item(ordinal=0)
    second = _snapshot_item(ordinal=2)
    snapshot = _snapshot(item=first)

    with pytest.raises(ValueError, match="content_hash"):
        MemorySnapshot.restore(
            id=snapshot.id,
            project_id=snapshot.project_id,
            conversation_id=snapshot.conversation_id,
            task_id=snapshot.task_id,
            base_version_id=snapshot.base_version_id,
            target_version_id=snapshot.target_version_id,
            snapshot_version=snapshot.snapshot_version,
            policy_version=snapshot.policy_version,
            source_watermark_cursor=snapshot.source_watermark_cursor,
            projection_generation=snapshot.projection_generation,
            projection_watermark_cursor=snapshot.projection_watermark_cursor,
            projection_state=snapshot.projection_state,
            status=snapshot.status,
            degraded_reason=snapshot.degraded_reason,
            content_hash="0" * 64,
            token_count=snapshot.token_count,
            items=snapshot.items,
            created_at=snapshot.created_at,
        )

    with pytest.raises(ValueError, match="contiguous"):
        MemorySnapshot.create(
            project_id=snapshot.project_id,
            conversation_id=snapshot.conversation_id,
            task_id=snapshot.task_id,
            base_version_id=snapshot.base_version_id,
            target_version_id=snapshot.target_version_id,
            policy_version=snapshot.policy_version,
            source_watermark_cursor=41,
            projection_generation=1,
            projection_watermark_cursor=41,
            projection_state=ProjectionState.READY,
            status=MemorySnapshotStatus.READY,
            degraded_reason=None,
            items=(first, second),
        )


def test_snapshot_status_must_match_projection_health() -> None:
    item = _snapshot_item()

    with pytest.raises(ValueError, match="READY"):
        MemorySnapshot.create(
            project_id=new_id(),
            conversation_id=new_id(),
            task_id=new_id(),
            base_version_id=None,
            target_version_id=None,
            policy_version="hermes-lexical-v1",
            source_watermark_cursor=10,
            projection_generation=1,
            projection_watermark_cursor=9,
            projection_state=ProjectionState.STALE,
            status=MemorySnapshotStatus.READY,
            degraded_reason=None,
            items=(item,),
        )

    degraded = MemorySnapshot.create(
        project_id=None,
        conversation_id=new_id(),
        task_id=new_id(),
        base_version_id=None,
        target_version_id=None,
        policy_version="hermes-lexical-v1",
        source_watermark_cursor=10,
        projection_generation=1,
        projection_watermark_cursor=9,
        projection_state=ProjectionState.STALE,
        status=MemorySnapshotStatus.DEGRADED,
        degraded_reason="MEMORY_PROJECTION_STALE",
        items=(item,),
    )

    assert degraded.status is MemorySnapshotStatus.DEGRADED


def test_snapshot_rejects_content_above_hard_token_ceiling() -> None:
    oversized = MemorySnapshotItem.create(
        ordinal=0,
        source_kind=MemorySourceKind.OBSERVATION,
        source_id=new_id(),
        source_revision=None,
        namespace=MemoryNamespace.CONVERSATION_DRAFT,
        selection_reason=MemorySelectionReason.RELATIONAL_FALLBACK,
        authority=MemoryAuthority.EXPLICIT_USER,
        score_components={"authority": 1.0},
        rendered_text="x",
        token_count=3_001,
    )

    with pytest.raises(ValueError, match="3,000"):
        _snapshot(item=oversized)


def test_projection_health_rejects_inconsistent_ready_state() -> None:
    with pytest.raises(ValueError, match="READY"):
        MemoryProjectionHealth(
            generation=1,
            state=ProjectionState.READY,
            source_watermark_cursor=8,
            projected_watermark_cursor=7,
            last_error_code=None,
            updated_at=datetime.now(UTC),
        )


def test_search_document_and_hit_require_safe_bounds() -> None:
    document = MemorySearchDocument.create(
        source_kind=MemorySourceKind.OBSERVATION,
        source_id=new_id(),
        source_revision=None,
        namespace=MemoryNamespace.CONVERSATION_DRAFT,
        project_id=None,
        conversation_id=new_id(),
        task_id=new_id(),
        version_id=None,
        language="und",
        normalized_text="remember the chosen framework",
        source_cursor=7,
        projection_generation=1,
    )
    hit = MemorySearchHit(
        document=document,
        lexical_score=0.5,
        exact_match=False,
    )

    assert document.content_hash
    assert hit.lexical_score == 0.5
    with pytest.raises(ValueError, match="lexical_score"):
        MemorySearchHit(document=document, lexical_score=float("nan"), exact_match=False)
