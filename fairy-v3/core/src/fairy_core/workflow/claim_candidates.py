from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.engine import Connection

from fairy_core.storage.schema import workflow_nodes, workflow_runs

CLAIM_CANDIDATE_BATCH_SIZE = 128


def ready_candidates(
    connection: Connection,
    *,
    tenant_id: str,
    now: datetime,
) -> Iterator[Mapping[str, Any]]:
    # Interleave one candidate per Run before taking its second candidate. This
    # prevents a large older Run from hiding every other Run behind a SQL LIMIT.
    ranked = (
        select(
            workflow_nodes.c.id,
            func.row_number()
            .over(
                partition_by=workflow_nodes.c.run_id,
                order_by=(workflow_nodes.c.created_at, workflow_nodes.c.id),
            )
            .label("fair_rank"),
        )
        .where(
            workflow_nodes.c.tenant_id == tenant_id,
            workflow_nodes.c.status == "ready",
            workflow_nodes.c.available_at <= now,
        )
        .subquery()
    )
    statement = (
        select(workflow_nodes, workflow_runs)
        .join(
            workflow_runs,
            and_(
                workflow_runs.c.tenant_id == workflow_nodes.c.tenant_id,
                workflow_runs.c.id == workflow_nodes.c.run_id,
                workflow_runs.c.active_plan_revision == workflow_nodes.c.plan_revision,
            ),
        )
        .join(ranked, ranked.c.id == workflow_nodes.c.id)
        .where(
            workflow_nodes.c.tenant_id == tenant_id,
            workflow_runs.c.status.in_(("queued", "running")),
            workflow_runs.c.pause_requested.is_(False),
        )
        .order_by(
            ranked.c.fair_rank,
            workflow_runs.c.parent_run_id.is_not(None).desc(),
            workflow_runs.c.updated_at,
            workflow_runs.c.id,
            workflow_nodes.c.created_at,
            workflow_nodes.c.id,
        )
    )
    offset = 0
    while True:
        # Selection does not mutate rows until the caller finishes iterating.
        # Page past conflicts instead of starving free resources on later pages.
        rows = (
            connection.execute(
                statement.limit(CLAIM_CANDIDATE_BATCH_SIZE).offset(offset),
            )
            .mappings()
            .all()
        )
        yield from rows
        if len(rows) < CLAIM_CANDIDATE_BATCH_SIZE:
            return
        offset += len(rows)
