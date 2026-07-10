from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from fairy_core.contracts.models import (
    MemoryClaimPromoteInput,
    MemoryClaimQuery,
    MemoryClaimResolveInput,
    MemoryClaimSupersedeInput,
    MemoryForgetInput,
    MemoryObservationQuery,
    MemoryObserveInput,
)
from fairy_core.domain.ids import new_id
from fairy_core.memory.models import MemoryNamespace


def test_memory_observe_accepts_only_user_controlled_fields() -> None:
    request = MemoryObserveInput(
        task_id=new_id(),
        content="Use compact navigation.",
        idempotency_key="memory:observe:1",
    )

    assert set(request.model_dump()) == {"task_id", "content", "idempotency_key"}
    for forbidden in (
        "tenant_id",
        "project_id",
        "conversation_id",
        "version_id",
        "scope_digest",
        "source_event_id",
        "actor",
        "authority",
        "namespace",
    ):
        with pytest.raises(ValidationError):
            MemoryObserveInput.model_validate(
                {
                    **request.model_dump(mode="json"),
                    forbidden: str(new_id()),
                }
            )


def test_memory_claim_mutations_do_not_accept_scope_or_identity() -> None:
    payload = {
        "task_id": str(new_id()),
        "observation_id": str(new_id()),
        "subject": "project",
        "predicate": "framework",
        "value": {"name": "React"},
        "normalized_text": "framework react",
        "user_confirmed": True,
        "idempotency_key": "memory:promote:1",
        "scope_digest": "0" * 64,
    }

    with pytest.raises(ValidationError):
        MemoryClaimPromoteInput.model_validate(payload)


def test_memory_claim_value_must_be_strict_json() -> None:
    with pytest.raises(ValidationError, match="valid JSON"):
        MemoryClaimPromoteInput(
            task_id=new_id(),
            observation_id=new_id(),
            subject="project",
            predicate="framework",
            value=math.nan,
            normalized_text="framework",
            user_confirmed=True,
            idempotency_key="memory:promote:nan",
        )


def test_memory_query_contracts_are_task_scoped() -> None:
    task_id = new_id()
    claim_id = new_id()
    observation_id = new_id()

    observation_query = MemoryObservationQuery(
        task_id=task_id,
        namespace=MemoryNamespace.CONVERSATION_DRAFT,
    )
    claim_query = MemoryClaimQuery(
        task_id=task_id,
        namespace=MemoryNamespace.PROJECT_CANONICAL,
    )
    supersede = MemoryClaimSupersedeInput(
        task_id=task_id,
        claim_id=claim_id,
        expected_revision=1,
        source_observation_ids=(observation_id,),
        value="React 19",
        normalized_text="react 19",
        user_confirmed=True,
        idempotency_key="memory:supersede:1",
    )
    resolve = MemoryClaimResolveInput(
        task_id=task_id,
        claim_id=claim_id,
        expected_revision=2,
        source_observation_ids=(observation_id,),
        resolved_claim_ids=(claim_id,),
        value="React 19.2",
        normalized_text="react 19.2",
        user_confirmed=True,
        idempotency_key="memory:resolve:1",
    )
    forget = MemoryForgetInput(
        task_id=task_id,
        target_kind="claim",
        target_id=claim_id,
        reason="No longer relevant",
        user_confirmed=True,
        idempotency_key="memory:forget:1",
    )

    assert set(observation_query.model_dump()) == {"task_id", "namespace"}
    assert set(claim_query.model_dump()) == {"task_id", "namespace"}
    assert supersede.expected_revision == 1
    assert resolve.resolved_claim_ids == (claim_id,)
    assert forget.target_kind == "claim"
