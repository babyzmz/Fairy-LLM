from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.engine import Connection

from fairy_core.commanding.schema import command_runs, domain_events
from fairy_core.memory.schema import (
    memory_access_log,
    memory_claim_revisions,
    memory_claims,
    memory_observations,
    memory_search_documents,
    memory_snapshot_items,
    memory_snapshots,
    memory_tombstones,
)
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.storage.schema import (
    annotation_documents,
    approvals,
    artifacts,
    asset_sets,
    assistant_imported_messages,
    assistant_messages,
    assistant_tool_invocations,
    assistant_turns,
    changesets,
    checkpoints,
    document_chunks,
    document_revisions,
    documents,
    edit_recipes,
    execution_plans,
    file_render_jobs,
    media_generation_jobs,
    preview_sessions,
    project_indexes,
    research_evidence,
    runtime_sessions,
    selection_references,
    task_steps,
    task_workspaces,
    tasks,
    turn_trace_steps,
    versions,
)


def purge_conversation_content(
    session: SqlAlchemySession,
    tenant_id: str,
    conversation_id: UUID,
) -> None:
    with session.write() as connection:
        conversation_key = str(conversation_id)
        task_ids = tuple(
            connection.execute(
                select(tasks.c.id).where(
                    tasks.c.tenant_id == tenant_id,
                    tasks.c.conversation_id == conversation_key,
                )
            ).scalars()
        )
        turn_ids = tuple(
            connection.execute(
                select(assistant_turns.c.id).where(
                    assistant_turns.c.tenant_id == tenant_id,
                    assistant_turns.c.conversation_id == conversation_key,
                )
            ).scalars()
        )
        scope = (
            assistant_messages.c.tenant_id == tenant_id,
            assistant_messages.c.conversation_id == conversation_key,
        )
        connection.execute(update(assistant_messages).where(*scope).values(content="[Deleted]"))
        connection.execute(
            update(assistant_imported_messages)
            .where(
                assistant_imported_messages.c.tenant_id == tenant_id,
                assistant_imported_messages.c.conversation_id == conversation_key,
            )
            .values(content="[Deleted]", source_hash="0" * 64)
        )
        connection.execute(
            update(tasks)
            .where(
                tasks.c.tenant_id == tenant_id,
                tasks.c.conversation_id == conversation_key,
            )
            .values(user_request="Deleted request", display_title="Deleted task")
        )
        connection.execute(
            update(assistant_turns)
            .where(
                assistant_turns.c.tenant_id == tenant_id,
                assistant_turns.c.conversation_id == conversation_key,
            )
            .values(model_selection=None, routing_decision=None)
        )
        connection.execute(
            update(changesets)
            .where(
                changesets.c.tenant_id == tenant_id,
                changesets.c.conversation_id == conversation_key,
            )
            .values(files=[], patches=[], reason="Deleted changeset")
        )
        connection.execute(
            delete(preview_sessions).where(
                preview_sessions.c.tenant_id == tenant_id,
                preview_sessions.c.conversation_id == conversation_key,
            )
        )
        connection.execute(
            delete(runtime_sessions).where(
                runtime_sessions.c.tenant_id == tenant_id,
                runtime_sessions.c.conversation_id == conversation_key,
            )
        )
        connection.execute(
            update(artifacts)
            .where(
                artifacts.c.tenant_id == tenant_id,
                artifacts.c.conversation_id == conversation_key,
            )
            .values(storage_location="deleted://artifact", metadata={})
        )
        connection.execute(
            update(media_generation_jobs)
            .where(
                media_generation_jobs.c.tenant_id == tenant_id,
                media_generation_jobs.c.conversation_id == conversation_key,
            )
            .values(output_path="deleted-output", request_spec={})
        )
        connection.execute(
            update(command_runs)
            .where(
                command_runs.c.tenant_id == tenant_id,
                command_runs.c.conversation_id == conversation_key,
            )
            .values(scope={}, input={})
        )
        connection.execute(
            update(domain_events)
            .where(
                domain_events.c.tenant_id == tenant_id,
                domain_events.c.conversation_id == conversation_key,
            )
            .values(message="Deleted event", payload={})
        )
        if task_ids:
            connection.execute(
                update(assistant_tool_invocations)
                .where(
                    assistant_tool_invocations.c.tenant_id == tenant_id,
                    assistant_tool_invocations.c.task_id.in_(task_ids),
                )
                .values(
                    arguments={},
                    public_summary=None,
                    model_content=None,
                    artifact_ids=[],
                )
            )
            connection.execute(
                update(execution_plans)
                .where(
                    execution_plans.c.tenant_id == tenant_id,
                    execution_plans.c.task_id.in_(task_ids),
                )
                .values(manifest={})
            )
            connection.execute(
                update(task_steps)
                .where(
                    task_steps.c.tenant_id == tenant_id,
                    task_steps.c.task_id.in_(task_ids),
                )
                .values(title="Deleted step")
            )
            connection.execute(
                update(task_workspaces)
                .where(
                    task_workspaces.c.tenant_id == tenant_id,
                    task_workspaces.c.task_id.in_(task_ids),
                )
                .values(root=".", editable_files=[], reference_files=[], constraints={})
            )
            connection.execute(
                update(approvals)
                .where(
                    approvals.c.tenant_id == tenant_id,
                    approvals.c.task_id.in_(task_ids),
                )
                .values(reason="Deleted approval")
            )
            connection.execute(
                update(checkpoints)
                .where(
                    checkpoints.c.tenant_id == tenant_id,
                    checkpoints.c.task_id.in_(task_ids),
                )
                .values(changed_files=[], evidence_artifact_ids=[])
            )
        if turn_ids:
            connection.execute(
                update(turn_trace_steps)
                .where(
                    turn_trace_steps.c.tenant_id == tenant_id,
                    turn_trace_steps.c.turn_id.in_(turn_ids),
                )
                .values(
                    public_summary="Deleted activity",
                    public_detail=None,
                    artifact_refs=[],
                )
            )
        _purge_documents(connection, tenant_id, conversation_key)
        _purge_memory(connection, tenant_id, conversation_key, task_ids)


def purge_project_content(
    session: SqlAlchemySession,
    tenant_id: str,
    project_id: UUID,
) -> None:
    with session.write() as connection:
        project_key = str(project_id)
        connection.execute(
            update(domain_events)
            .where(
                domain_events.c.tenant_id == tenant_id,
                domain_events.c.project_id == project_key,
                domain_events.c.conversation_id.is_(None),
            )
            .values(message="Deleted event", payload={})
        )
        claim_ids = tuple(
            connection.execute(
                select(memory_claims.c.id).where(
                    memory_claims.c.tenant_id == tenant_id,
                    memory_claims.c.project_id == project_key,
                )
            ).scalars()
        )
        _delete_memory_claims(connection, tenant_id, claim_ids)
        connection.execute(
            delete(memory_search_documents).where(
                memory_search_documents.c.tenant_id == tenant_id,
                memory_search_documents.c.project_id == project_key,
            )
        )


def purge_workspace_content(
    session: SqlAlchemySession,
    tenant_id: str,
    workspace_id: UUID,
) -> None:
    with session.write() as connection:
        workspace_key = str(workspace_id)
        connection.execute(
            delete(file_render_jobs).where(
                file_render_jobs.c.tenant_id == tenant_id,
                file_render_jobs.c.workspace_id == workspace_key,
            )
        )
        for table in (
            annotation_documents,
            edit_recipes,
            selection_references,
            asset_sets,
            project_indexes,
        ):
            connection.execute(
                delete(table).where(
                    table.c.tenant_id == tenant_id,
                    table.c.workspace_id == workspace_key,
                )
            )
        connection.execute(
            update(versions)
            .where(
                versions.c.tenant_id == tenant_id,
                versions.c.workspace_id == workspace_key,
            )
            .values(project_root=".")
        )


def _purge_documents(connection: Connection, tenant_id: str, conversation_key: str) -> None:
    document_ids = tuple(
        connection.execute(
            select(documents.c.id).where(
                documents.c.tenant_id == tenant_id,
                documents.c.conversation_id == conversation_key,
            )
        ).scalars()
    )
    connection.execute(
        delete(research_evidence).where(
            research_evidence.c.tenant_id == tenant_id,
            research_evidence.c.conversation_id == conversation_key,
        )
    )
    if document_ids:
        connection.execute(
            delete(document_chunks).where(
                document_chunks.c.tenant_id == tenant_id,
                document_chunks.c.document_id.in_(document_ids),
            )
        )
        connection.execute(
            delete(document_revisions).where(
                document_revisions.c.tenant_id == tenant_id,
                document_revisions.c.document_id.in_(document_ids),
            )
        )
    connection.execute(
        delete(documents).where(
            documents.c.tenant_id == tenant_id,
            documents.c.conversation_id == conversation_key,
        )
    )


def _purge_memory(
    connection: Connection,
    tenant_id: str,
    conversation_key: str,
    task_ids: tuple[str, ...],
) -> None:
    snapshot_ids = tuple(
        connection.execute(
            select(memory_snapshots.c.id).where(
                memory_snapshots.c.tenant_id == tenant_id,
                memory_snapshots.c.conversation_id == conversation_key,
            )
        ).scalars()
    )
    if snapshot_ids:
        connection.execute(
            delete(memory_access_log).where(
                memory_access_log.c.tenant_id == tenant_id,
                memory_access_log.c.snapshot_id.in_(snapshot_ids),
            )
        )
        connection.execute(
            delete(memory_snapshot_items).where(
                memory_snapshot_items.c.tenant_id == tenant_id,
                memory_snapshot_items.c.snapshot_id.in_(snapshot_ids),
            )
        )
    connection.execute(
        delete(memory_snapshots).where(
            memory_snapshots.c.tenant_id == tenant_id,
            memory_snapshots.c.conversation_id == conversation_key,
        )
    )
    task_scope = memory_claims.c.task_id.in_(task_ids) if task_ids else False
    claim_ids = tuple(
        connection.execute(
            select(memory_claims.c.id).where(
                memory_claims.c.tenant_id == tenant_id,
                or_(memory_claims.c.conversation_id == conversation_key, task_scope),
            )
        ).scalars()
    )
    _delete_memory_claims(connection, tenant_id, claim_ids)
    search_task_scope = memory_search_documents.c.task_id.in_(task_ids) if task_ids else False
    connection.execute(
        delete(memory_search_documents).where(
            memory_search_documents.c.tenant_id == tenant_id,
            or_(
                memory_search_documents.c.conversation_id == conversation_key,
                search_task_scope,
            ),
        )
    )
    connection.execute(
        delete(memory_observations).where(
            memory_observations.c.tenant_id == tenant_id,
            memory_observations.c.conversation_id == conversation_key,
        )
    )


def _delete_memory_claims(
    connection: Connection,
    tenant_id: str,
    claim_ids: tuple[str, ...],
) -> None:
    if not claim_ids:
        return
    connection.execute(
        delete(memory_tombstones).where(
            memory_tombstones.c.tenant_id == tenant_id,
            memory_tombstones.c.target_id.in_(claim_ids),
        )
    )
    connection.execute(
        delete(memory_claim_revisions).where(
            memory_claim_revisions.c.tenant_id == tenant_id,
            memory_claim_revisions.c.claim_id.in_(claim_ids),
        )
    )
    connection.execute(
        delete(memory_claims).where(
            memory_claims.c.tenant_id == tenant_id,
            memory_claims.c.id.in_(claim_ids),
        )
    )
