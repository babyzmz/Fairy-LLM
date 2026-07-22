from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import Index, Table, text


def build_state_indexes(tables: Mapping[str, Table]) -> None:
    def table(name: str) -> Table:
        return tables[name]

    projects = table("projects")
    conversations = table("conversations")
    versions = table("versions")
    tasks = table("tasks")
    turns = table("assistant_turns")
    attempts = table("assistant_provider_attempts")
    messages = table("assistant_messages")
    invocations = table("assistant_tool_invocations")
    changesets = table("changesets")
    approvals = table("approvals")
    checkpoints = table("checkpoints")
    runtimes = table("runtime_sessions")
    previews = table("preview_sessions")
    artifacts = table("artifacts")
    documents = table("documents")
    revisions = table("document_revisions")
    chunks = table("document_chunks")
    evidence = table("research_evidence")

    Index("ix_core_projects_tenant_updated", projects.c.tenant_id, projects.c.updated_at)
    Index(
        "ix_core_conversations_tenant_project",
        conversations.c.tenant_id,
        conversations.c.project_id,
    )
    Index("ix_core_versions_tenant_project", versions.c.tenant_id, versions.c.project_id)
    Index("ix_core_tasks_tenant_status", tasks.c.tenant_id, tasks.c.status, tasks.c.created_at)
    Index(
        "ix_core_assistant_turns_tenant_task",
        turns.c.tenant_id,
        turns.c.task_id,
        turns.c.created_at,
    )
    Index(
        "ix_core_provider_attempts_tenant_turn",
        attempts.c.tenant_id,
        attempts.c.turn_id,
        attempts.c.model_round,
        attempts.c.attempt_number,
    )
    Index(
        "ix_core_assistant_messages_tenant_conversation",
        messages.c.tenant_id,
        messages.c.conversation_id,
        messages.c.sequence,
    )
    Index(
        "ix_core_assistant_tool_invocations_tenant_turn",
        invocations.c.tenant_id,
        invocations.c.turn_id,
        invocations.c.sequence,
    )
    Index("ix_core_changesets_tenant_task", changesets.c.tenant_id, changesets.c.task_id)
    Index("ix_core_approvals_tenant_task", approvals.c.tenant_id, approvals.c.task_id)
    Index("ix_core_checkpoints_tenant_task", checkpoints.c.tenant_id, checkpoints.c.task_id)
    Index(
        "ix_core_runtime_sessions_tenant_task",
        runtimes.c.tenant_id,
        runtimes.c.task_id,
        runtimes.c.created_at,
    )
    Index(
        "ix_core_preview_sessions_tenant_conversation",
        previews.c.tenant_id,
        previews.c.conversation_id,
        previews.c.created_at,
    )
    Index(
        "ix_core_preview_sessions_tenant_active_access",
        previews.c.tenant_id,
        previews.c.status,
        previews.c.last_accessed_at,
        previews.c.id,
    )
    Index(
        "uq_core_preview_sessions_active_task",
        previews.c.tenant_id,
        previews.c.task_id,
        unique=True,
        sqlite_where=text("status IN ('created', 'starting', 'ready', 'stopping')"),
        postgresql_where=text("status IN ('created', 'starting', 'ready', 'stopping')"),
    )
    Index(
        "ix_core_artifacts_tenant_task",
        artifacts.c.tenant_id,
        artifacts.c.task_id,
        artifacts.c.created_at,
    )
    Index(
        "ix_core_documents_tenant_conversation",
        documents.c.tenant_id,
        documents.c.conversation_id,
        documents.c.status,
        documents.c.created_at,
    )
    Index(
        "ix_core_documents_tenant_project",
        documents.c.tenant_id,
        documents.c.project_id,
        documents.c.status,
        documents.c.created_at,
    )
    Index(
        "ix_core_document_revisions_tenant_document",
        revisions.c.tenant_id,
        revisions.c.document_id,
        revisions.c.revision,
    )
    Index(
        "ix_core_document_chunks_tenant_document",
        chunks.c.tenant_id,
        chunks.c.document_id,
        chunks.c.revision,
        chunks.c.ordinal,
    )
    Index("uq_core_document_chunks_fts_rowid", chunks.c.fts_rowid, unique=True)
    Index(
        "ix_core_research_evidence_tenant_artifact",
        evidence.c.tenant_id,
        evidence.c.artifact_id,
        evidence.c.ordinal,
    )


__all__ = ["build_state_indexes"]
