from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import and_, select

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
    return attach_workflow_summaries(session, tenant_id=tenant_id, turns=(turn,))[0]


def attach_workflow_summaries(
    session: SqlAlchemySession,
    *,
    tenant_id: str,
    turns: tuple[AssistantTurn, ...],
) -> tuple[AssistantTurn, ...]:
    run_ids = {str(turn.workflow_run_id) for turn in turns if turn.workflow_run_id is not None}
    if not run_ids:
        return turns
    with session.read() as connection:
        runs = {
            str(row["id"]): row
            for row in connection.execute(
                select(workflow_runs).where(
                    workflow_runs.c.tenant_id == tenant_id,
                    workflow_runs.c.id.in_(run_ids),
                ),
            ).mappings()
        }
        nodes = defaultdict(list)
        for row in connection.execute(
            select(
                workflow_nodes.c.run_id,
                workflow_nodes.c.id,
                workflow_nodes.c.status,
                workflow_nodes.c.kind,
                workflow_nodes.c.public_summary,
            )
            .join(
                workflow_runs,
                and_(
                    workflow_runs.c.tenant_id == workflow_nodes.c.tenant_id,
                    workflow_runs.c.id == workflow_nodes.c.run_id,
                    workflow_runs.c.active_plan_revision == workflow_nodes.c.plan_revision,
                ),
            )
            .where(workflow_nodes.c.tenant_id == tenant_id, workflow_nodes.c.run_id.in_(run_ids)),
        ).mappings():
            nodes[str(row["run_id"])].append(row)
    for turn in turns:
        if turn.workflow_run_id is None:
            continue
        run = runs.get(str(turn.workflow_run_id))
        if run is None:
            raise ValueError("Assistant Turn is bound to a missing Workflow Run")
        _attach_projection(turn, run, nodes[str(turn.workflow_run_id)])
    return turns


def _attach_projection(turn, run, nodes) -> None:
    run_id = turn.workflow_run_id
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


def _datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


__all__ = ["attach_workflow_summaries", "attach_workflow_summary"]
