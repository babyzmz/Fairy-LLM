from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import Engine

from fairy_core.assistant.sqlite_migrations import (
    migrate_assistant_workflow_binding,
    remove_legacy_assistant_turn_work,
)
from fairy_core.commanding.schema import command_metadata
from fairy_core.commanding.sqlite_migrations import (
    migrate_pre_tenant_ledger,
    prepare_pre_tenant_schema,
)
from fairy_core.documents.sqlite_fts import initialize_document_sqlite_fts
from fairy_core.knowledge.schema import knowledge_metadata
from fairy_core.memory.schema import memory_metadata
from fairy_core.memory.sqlite_fts import initialize_sqlite_fts
from fairy_core.persistence.sqlite_split_migration import import_split_sqlite_databases
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.storage.assistant_interpretation_sqlite_migrations import (
    migrate_assistant_request_interpretations,
    migrate_assistant_waiting_for_input,
)
from fairy_core.storage.assistant_schedule_sqlite_migrations import (
    migrate_assistant_schedule_interpretation,
    migrate_assistant_schedule_operation_mode,
)
from fairy_core.storage.schema import state_metadata
from fairy_core.storage.sqlite_engine import create_sqlite_engine
from fairy_core.storage.sqlite_evidence_migration import migrate_assistant_evidence
from fairy_core.storage.sqlite_migrations import (
    migrate_assistant_model_routing,
    migrate_checkpoint_evidence,
    migrate_generic_approval,
    migrate_harness_persona,
    migrate_harness_persona_instruction,
    migrate_history_metadata,
    migrate_knowledge_harness_binding,
    migrate_knowledge_manifest_tools,
    migrate_knowledge_sync_leases,
    migrate_mcp_request_results,
    migrate_pre_tenant_schema,
    migrate_preview_runtime_pool,
    migrate_project_lifecycle,
    migrate_runtime_graph,
    migrate_runtime_workspace_binding,
    migrate_task_snapshot_binding,
    migrate_workspace_identity,
)


def create_sqlite_core_engine(
    path: Path,
    *,
    tenant_id: str = "local",
    legacy_state_path: Path | None = None,
    legacy_ledger_path: Path | None = None,
) -> Engine:
    tenant_id = normalize_tenant_id(tenant_id)
    engine = create_sqlite_engine(path)
    try:
        prepare_pre_tenant_schema(engine)
        state_metadata.create_all(engine)
        migrate_assistant_request_interpretations(engine)
        migrate_assistant_waiting_for_input(engine)
        migrate_assistant_schedule_operation_mode(engine)
        migrate_assistant_schedule_interpretation(engine)
        migrate_assistant_model_routing(engine)
        migrate_assistant_workflow_binding(engine)
        remove_legacy_assistant_turn_work(engine)
        migrate_assistant_evidence(engine)
        migrate_workspace_identity(engine)
        migrate_runtime_workspace_binding(engine)
        migrate_runtime_graph(engine)
        migrate_preview_runtime_pool(engine)
        initialize_document_sqlite_fts(engine)
        migrate_task_snapshot_binding(engine)
        migrate_knowledge_harness_binding(engine)
        migrate_history_metadata(engine)
        migrate_project_lifecycle(engine)
        migrate_generic_approval(engine)
        migrate_checkpoint_evidence(engine)
        migrate_mcp_request_results(engine)
        command_metadata.create_all(engine)
        memory_metadata.create_all(engine)
        knowledge_metadata.create_all(engine)
        migrate_knowledge_manifest_tools(engine)
        migrate_harness_persona(engine)
        migrate_harness_persona_instruction(engine)
        migrate_knowledge_sync_leases(engine)
        initialize_sqlite_fts(engine)
        migrate_pre_tenant_schema(engine, tenant_id=tenant_id)
        migrate_pre_tenant_ledger(engine, tenant_id=tenant_id)
        import_split_sqlite_databases(
            engine,
            destination_path=path,
            state_path=legacy_state_path,
            ledger_path=legacy_ledger_path,
            tenant_id=tenant_id,
        )
    except BaseException:
        engine.dispose()
        raise
    return engine
