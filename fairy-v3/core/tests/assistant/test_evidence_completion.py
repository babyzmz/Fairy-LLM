from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fairy_core.assistant.durable_context import durable_tool_context
from fairy_core.assistant.evidence import (
    EvidenceDraft,
    EvidenceRequirementKind,
    EvidenceSourceKind,
    seal_evidence_drafts,
)
from fairy_core.assistant.models import ToolInvocation
from fairy_core.assistant.routing import (
    QWEN_FREE_MODEL_ID,
    RoutingComplexity,
    RoutingDecision,
    RoutingTaskKind,
)
from fairy_core.assistant.turn_lifecycle import _evidence_completion_issue
from tests.assistant.test_models import _scope, _task


def test_completion_requires_same_turn_unexpired_receipts_for_every_requirement() -> None:
    task = _task()
    scope = _scope(task)
    from fairy_core.assistant.models import AssistantTurn

    turn = AssistantTurn.create(
        task=task,
        scope=scope,
        profile_id="local-default",
        idempotency_key="evidence-turn",
    )
    turn.bind_routing(
        RoutingDecision(
            task_kind=RoutingTaskKind.GENERAL,
            complexity=RoutingComplexity.LOW,
            primary_model_id=QWEN_FREE_MODEL_ID,
            reviewer_model_id=None,
            media_model_id=None,
            estimated_output_tokens=1_024,
            estimated_cost_usd="0",
            cost_estimate_known=True,
            approval_required=False,
            requires_workspace_changes=False,
            public_summary="Current public evidence required",
            evidence_requirements=(EvidenceRequirementKind.WEB_CURRENT,),
        )
    )
    invocation = ToolInvocation.create(
        turn=turn,
        model_round=1,
        sequence=1,
        provider_call_id="web-call",
        tool_name="web.fetch",
        scope_digest=scope.scope_digest,
        arguments={"url": "https://example.com/current"},
    )
    invocation.queue(command_run_id=uuid4())
    invocation.start()
    observed = datetime.now(UTC)
    receipt = seal_evidence_drafts(
        (
            EvidenceDraft(
                requirement_kind=EvidenceRequirementKind.WEB_CURRENT,
                source_kind=EvidenceSourceKind.WEB_DOCUMENT,
                public_label="Current source",
                safe_url="https://example.com/current?secret=removed",
                content_hash="a" * 64,
                observed_at=observed,
                expires_at=observed + timedelta(minutes=5),
            ),
        ),
        invocation_id=invocation.id,
        turn_id=turn.id,
        tool_name=invocation.tool_name,
        scope=scope,
    )[0]
    invocation.complete(
        public_summary="Fetched current source",
        model_content="current",
        artifact_ids=(),
        evidence_receipts=(receipt,),
    )

    # A new model node/restart must recover the sealed receipt even when the
    # original body is too large for the bounded provider projection.
    projected = durable_tool_context((replace(invocation, model_content="x" * 20_000),))
    assert projected[-1].tool_call_id == invocation.provider_call_id
    assert f'"receipt_id":"{receipt.id}"' in projected[-1].content
    assert "context projection truncated" in projected[-1].content
    assert sum(len(item.content) for item in projected) <= 40_000

    assert _evidence_completion_issue(turn, (invocation,), None).startswith(
        "EVIDENCE_CITATION_REQUIRED"
    )
    assert _evidence_completion_issue(turn, (invocation,), (str(uuid4()),)).startswith(
        "EVIDENCE_CITATION_INVALID"
    )
    assert _evidence_completion_issue(turn, (invocation,), (str(receipt.id),)) is None
    assert receipt.safe_url == "https://example.com/current"


def test_completion_rejects_expired_current_state_receipt() -> None:
    task = _task()
    scope = _scope(task)
    from fairy_core.assistant.models import AssistantTurn

    turn = AssistantTurn.create(
        task=task,
        scope=scope,
        profile_id="local-default",
        idempotency_key="expired-evidence-turn",
    )
    turn.bind_routing(
        RoutingDecision(
            task_kind=RoutingTaskKind.GENERAL,
            complexity=RoutingComplexity.LOW,
            primary_model_id=QWEN_FREE_MODEL_ID,
            reviewer_model_id=None,
            media_model_id=None,
            estimated_output_tokens=1_024,
            estimated_cost_usd="0",
            cost_estimate_known=True,
            approval_required=False,
            requires_workspace_changes=False,
            public_summary="Current public evidence required",
            evidence_requirements=(EvidenceRequirementKind.WEB_CURRENT,),
        )
    )
    invocation = ToolInvocation.create(
        turn=turn,
        model_round=1,
        sequence=1,
        provider_call_id="old-web-call",
        tool_name="web.fetch",
        scope_digest=scope.scope_digest,
        arguments={"url": "https://example.com/old"},
    )
    invocation.queue(command_run_id=uuid4())
    invocation.start()
    observed = datetime.now(UTC) - timedelta(minutes=2)
    receipt = seal_evidence_drafts(
        (
            EvidenceDraft(
                requirement_kind=EvidenceRequirementKind.WEB_CURRENT,
                source_kind=EvidenceSourceKind.WEB_DOCUMENT,
                public_label="Expired source",
                safe_url="https://example.com/old",
                content_hash="b" * 64,
                observed_at=observed,
                expires_at=observed + timedelta(minutes=1),
            ),
        ),
        invocation_id=invocation.id,
        turn_id=turn.id,
        tool_name=invocation.tool_name,
        scope=scope,
    )[0]
    invocation.complete(
        public_summary="Fetched old source",
        model_content="old",
        artifact_ids=(),
        evidence_receipts=(receipt,),
    )

    issue = _evidence_completion_issue(turn, (invocation,), (str(receipt.id),))
    assert issue is not None and issue.startswith("EVIDENCE_EXPIRED")
