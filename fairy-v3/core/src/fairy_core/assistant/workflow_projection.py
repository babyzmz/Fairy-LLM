from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from fairy_core.assistant.models import AssistantTurn, AssistantWorkflowSummary
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.storage.schema import workflow_nodes, workflow_runs
from fairy_core.workflow.models import (
    WorkflowBudgetTier,
    WorkflowNodeStatus,
    WorkflowRunStatus,
)


def attach_workflow_summary(
    session: SqlAlchemySession,
    *,
    tenant_id: str,
    turn: AssistantTurn,
) -> AssistantTurn:
    run_id = turn.workflow_run_id
    if run_id is None:
        return turn
    run, nodes = _load_active_plan(session, tenant_id=tenant_id, run_id=run_id)
    priority = {
        WorkflowNodeStatus.RUNNING.value: 0,
        WorkflowNodeStatus.WAITING_FOR_APPROVAL.value: 1,
        WorkflowNodeStatus.READY.value: 2,
        WorkflowNodeStatus.PENDING.value: 3,
    }
    active = min(
        (node for node in nodes if node["status"] in priority),
        key=lambda node: (priority[str(node["status"])], str(node["id"])),
        default=None,
    )
    terminal = {
        WorkflowNodeStatus.SUCCEEDED.value,
        WorkflowNodeStatus.FAILED.value,
        WorkflowNodeStatus.CANCELLED.value,
        WorkflowNodeStatus.SKIPPED.value,
        WorkflowNodeStatus.SUPERSEDED.value,
    }
    turn.workflow_summary = AssistantWorkflowSummary(
        run_id=run_id,
        status=WorkflowRunStatus(run["status"]),
        budget_tier=WorkflowBudgetTier(run["budget_tier"]),
        active_plan_revision=int(run["active_plan_revision"]),
        current_phase=str(active["kind"]) if active is not None else None,
        public_summary=str(active["public_summary"]) if active is not None else None,
        completed_nodes=sum(node["status"] in terminal for node in nodes),
        total_nodes=len(nodes),
        model_rounds_used=int(run["model_rounds_used"]),
        max_model_rounds=int(run["max_model_rounds"]),
        tool_invocations_used=int(run["tool_invocations_used"]),
        max_tool_invocations=int(run["max_tool_invocations"]),
        pause_requested=bool(run["pause_requested"]),
        updated_at=_datetime(run["updated_at"]),
    )
    return turn


def _load_active_plan(
    session: SqlAlchemySession,
    *,
    tenant_id: str,
    run_id: UUID,
):
    with session.read() as connection:
        run = (
            connection.execute(
                select(workflow_runs).where(
                    workflow_runs.c.tenant_id == tenant_id,
                    workflow_runs.c.id == str(run_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        if run is None:
            raise ValueError("Assistant Turn is bound to a missing Workflow Run")
        nodes = (
            connection.execute(
                select(workflow_nodes)
                .where(
                    workflow_nodes.c.tenant_id == tenant_id,
                    workflow_nodes.c.run_id == str(run_id),
                    workflow_nodes.c.plan_revision == int(run["active_plan_revision"]),
                )
                .order_by(workflow_nodes.c.created_at, workflow_nodes.c.id)
            )
            .mappings()
            .all()
        )
    return run, nodes


def _datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


__all__ = ["attach_workflow_summary"]
