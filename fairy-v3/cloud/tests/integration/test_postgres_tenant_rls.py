from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

POSTGRES_DSN = os.environ.get("FAIRY_TEST_POSTGRES_DSN")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_DSN, reason="FAIRY_TEST_POSTGRES_DSN is not set"),
]


def test_postgres_forced_rls_isolates_same_ids_across_tenants() -> None:
    assert POSTGRES_DSN is not None
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    old_dsn = os.environ.get("FAIRY_POSTGRES_DSN")
    os.environ["FAIRY_POSTGRES_DSN"] = POSTGRES_DSN
    try:
        command.upgrade(config, "head")
        asyncio.run(_run_rls_scenario(POSTGRES_DSN))
    finally:
        if old_dsn is None:
            os.environ.pop("FAIRY_POSTGRES_DSN", None)
        else:
            os.environ["FAIRY_POSTGRES_DSN"] = old_dsn


async def _run_rls_scenario(dsn: str) -> None:
    suffix = uuid4().hex
    role = f"fairy_rls_{suffix}"
    tenant_a = f"tenant-a-{suffix}"
    tenant_b = f"tenant-b-{suffix}"
    project_id = str(uuid4())
    conversation_id = str(uuid4())
    task_id = str(uuid4())
    version_id = str(uuid4())
    runtime_id = str(uuid4())
    preview_id = str(uuid4())
    artifact_id = str(uuid4())
    event_id = str(uuid4())
    memory_document_id = str(uuid4())
    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as admin:
            await admin.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS'))
            await admin.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            await admin.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                    f'IN SCHEMA public TO "{role}"'
                )
            )
            await admin.execute(
                text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
            )

        async with (
            engine.connect() as connection_a,
            engine.connect() as connection_b,
            connection_a.begin(),
            connection_b.begin(),
        ):
            await _set_role_and_tenant(connection_a, role=role, tenant_id=tenant_a)
            await _set_role_and_tenant(connection_b, role=role, tenant_id=tenant_b)
            await _insert_tenant_project_event(
                connection_a,
                tenant_id=tenant_a,
                subject_id=f"user-a-{suffix}",
                project_id=project_id,
                conversation_id=conversation_id,
                task_id=task_id,
                version_id=version_id,
                runtime_id=runtime_id,
                preview_id=preview_id,
                artifact_id=artifact_id,
                event_id=event_id,
                memory_document_id=memory_document_id,
                memory_text="alpha tenant memory",
                marker="alpha",
            )
            await _insert_tenant_project_event(
                connection_b,
                tenant_id=tenant_b,
                subject_id=f"user-b-{suffix}",
                project_id=project_id,
                conversation_id=conversation_id,
                task_id=task_id,
                version_id=version_id,
                runtime_id=runtime_id,
                preview_id=preview_id,
                artifact_id=artifact_id,
                event_id=event_id,
                memory_document_id=memory_document_id,
                memory_text="beta tenant memory",
                marker="beta",
            )

            projects_a = (
                (
                    await connection_a.execute(
                        text("SELECT tenant_id FROM core_projects WHERE id = :project_id"),
                        {"project_id": project_id},
                    )
                )
                .scalars()
                .all()
            )
            projects_b = (
                (
                    await connection_b.execute(
                        text("SELECT tenant_id FROM core_projects WHERE id = :project_id"),
                        {"project_id": project_id},
                    )
                )
                .scalars()
                .all()
            )
            assert projects_a == [tenant_a]
            assert projects_b == [tenant_b]

            updated = await connection_a.execute(
                text("UPDATE core_projects SET revision = 1 WHERE id = :project_id"),
                {"project_id": project_id},
            )
            assert updated.rowcount == 1
            revision_b = (
                await connection_b.execute(
                    text("SELECT revision FROM core_projects WHERE id = :project_id"),
                    {"project_id": project_id},
                )
            ).scalar_one()
            assert revision_b == 0

            events_a = (
                (
                    await connection_a.execute(
                        text("SELECT tenant_id FROM domain_events WHERE event_id = :event_id"),
                        {"event_id": event_id},
                    )
                )
                .scalars()
                .all()
            )
            events_b = (
                (
                    await connection_b.execute(
                        text("SELECT tenant_id FROM domain_events WHERE event_id = :event_id"),
                        {"event_id": event_id},
                    )
                )
                .scalars()
                .all()
            )
            assert events_a == [tenant_a]
            assert events_b == [tenant_b]

            memory_a = (
                await connection_a.execute(
                    text(
                        "SELECT tenant_id, normalized_text "
                        "FROM memory_search_documents WHERE id = :document_id"
                    ),
                    {"document_id": memory_document_id},
                )
            ).one()
            memory_b = (
                await connection_b.execute(
                    text(
                        "SELECT tenant_id, normalized_text "
                        "FROM memory_search_documents WHERE id = :document_id"
                    ),
                    {"document_id": memory_document_id},
                )
            ).one()
            assert tuple(memory_a) == (tenant_a, "alpha tenant memory")
            assert tuple(memory_b) == (tenant_b, "beta tenant memory")

            runtime_a = (
                await connection_a.execute(
                    text(
                        "SELECT tenant_id, executor FROM core_runtime_sessions "
                        "WHERE id = :runtime_id"
                    ),
                    {"runtime_id": runtime_id},
                )
            ).one()
            runtime_b = (
                await connection_b.execute(
                    text(
                        "SELECT tenant_id, executor FROM core_runtime_sessions "
                        "WHERE id = :runtime_id"
                    ),
                    {"runtime_id": runtime_id},
                )
            ).one()
            assert tuple(runtime_a) == (tenant_a, "worker-alpha")
            assert tuple(runtime_b) == (tenant_b, "worker-beta")

            artifact_a = (
                await connection_a.execute(
                    text(
                        "SELECT tenant_id, metadata ->> 'marker' FROM core_artifacts "
                        "WHERE id = :artifact_id"
                    ),
                    {"artifact_id": artifact_id},
                )
            ).one()
            artifact_b = (
                await connection_b.execute(
                    text(
                        "SELECT tenant_id, metadata ->> 'marker' FROM core_artifacts "
                        "WHERE id = :artifact_id"
                    ),
                    {"artifact_id": artifact_id},
                )
            ).one()
            assert tuple(artifact_a) == (tenant_a, "alpha")
            assert tuple(artifact_b) == (tenant_b, "beta")

            updated_runtime = await connection_a.execute(
                text(
                    "UPDATE core_runtime_sessions SET revision = revision + 1 "
                    "WHERE id = :runtime_id"
                ),
                {"runtime_id": runtime_id},
            )
            assert updated_runtime.rowcount == 1
            revision_b = (
                await connection_b.execute(
                    text("SELECT revision FROM core_runtime_sessions WHERE id = :runtime_id"),
                    {"runtime_id": runtime_id},
                )
            ).scalar_one()
            assert revision_b == 0
    finally:
        async with engine.begin() as admin:
            for table_name in (
                "core_artifacts",
                "core_preview_sessions",
                "core_runtime_sessions",
                "memory_search_documents",
                "domain_events",
                "core_tasks",
                "core_versions",
                "core_conversations",
                "core_projects",
                "core_tenants",
            ):
                await admin.execute(
                    text(f'DELETE FROM "{table_name}" WHERE tenant_id IN (:tenant_a, :tenant_b)'),
                    {"tenant_a": tenant_a, "tenant_b": tenant_b},
                )
            await admin.execute(text(f'DROP OWNED BY "{role}"'))
            await admin.execute(text(f'DROP ROLE "{role}"'))
        await engine.dispose()


async def _set_role_and_tenant(
    connection: AsyncConnection,
    *,
    role: str,
    tenant_id: str,
) -> None:
    await connection.execute(text(f'SET LOCAL ROLE "{role}"'))
    await connection.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )


async def _insert_tenant_project_event(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    subject_id: str,
    project_id: str,
    conversation_id: str,
    task_id: str,
    version_id: str,
    runtime_id: str,
    preview_id: str,
    artifact_id: str,
    event_id: str,
    memory_document_id: str,
    memory_text: str,
    marker: str,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO core_tenants (tenant_id, subject_id)
            VALUES (:tenant_id, :subject_id)
            """
        ),
        {"tenant_id": tenant_id, "subject_id": subject_id},
    )
    await connection.execute(
        text(
            """
            INSERT INTO core_projects (
                tenant_id, id, name, residency, revision, created_at, updated_at
            ) VALUES (
                :tenant_id, :project_id, :project_id, 'synced', 0, now(), now()
            )
            """
        ),
        {"tenant_id": tenant_id, "project_id": project_id},
    )
    await connection.execute(
        text(
            """
            INSERT INTO core_conversations (
                tenant_id, id, project_id, workspace_type,
                created_at, updated_at
            ) VALUES (
                :tenant_id, :conversation_id, :project_id, 'project_chat',
                now(), now()
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "project_id": project_id,
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO core_tasks (
                tenant_id, id, project_id, conversation_id, user_request,
                operation_mode, target_version_id, execution_target, status,
                idempotency_key, created_at, updated_at
            ) VALUES (
                :tenant_id, :task_id, :project_id, :conversation_id, 'Preview',
                'continue_current_draft', :version_id, 'local', 'executing',
                'task:rls', now(), now()
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "task_id": task_id,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "version_id": version_id,
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO core_versions (
                tenant_id, id, project_id, source_conversation_id,
                source_task_id, project_root, visibility, created_at
            ) VALUES (
                :tenant_id, :version_id, :project_id, :conversation_id,
                :task_id, :project_root, 'chat_draft', now()
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "version_id": version_id,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "task_id": task_id,
            "project_root": f"/fairy/{tenant_id}/{version_id}",
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO core_runtime_sessions (
                tenant_id, id, project_id, conversation_id, task_id, version_id,
                project_root, execution_target, kind, executor, executor_handle,
                port, status, health, idempotency_key, revision,
                created_at, updated_at
            ) VALUES (
                :tenant_id, :runtime_id, :project_id, :conversation_id, :task_id,
                :version_id, :project_root, 'local', 'static_site', :executor,
                :executor_handle, 43125, 'running', 'healthy', 'runtime:rls', 0,
                now(), now()
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "runtime_id": runtime_id,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "task_id": task_id,
            "version_id": version_id,
            "project_root": f"/fairy/{tenant_id}/{version_id}",
            "executor": f"worker-{marker}",
            "executor_handle": f"static:{marker}",
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO core_preview_sessions (
                tenant_id, id, project_id, conversation_id, task_id, version_id,
                runtime_id, project_root, execution_target, url, visibility,
                status, health, idempotency_key, revision, created_at, updated_at
            ) VALUES (
                :tenant_id, :preview_id, :project_id, :conversation_id, :task_id,
                :version_id, :runtime_id, :project_root, 'local',
                'http://127.0.0.1:43125/rls/', 'chat_draft', 'ready', 'healthy',
                'preview:rls', 0, now(), now()
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "preview_id": preview_id,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "task_id": task_id,
            "version_id": version_id,
            "runtime_id": runtime_id,
            "project_root": f"/fairy/{tenant_id}/{version_id}",
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO core_artifacts (
                tenant_id, id, project_id, conversation_id, task_id, version_id,
                artifact_type, visibility, storage_location, media_type,
                byte_length, content_hash, metadata, created_at
            ) VALUES (
                :tenant_id, :artifact_id, :project_id, :conversation_id, :task_id,
                :version_id, 'preview_manifest', 'conversation', :storage_location,
                'application/json', 2, :content_hash,
                jsonb_build_object('marker', :marker), now()
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "artifact_id": artifact_id,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "task_id": task_id,
            "version_id": version_id,
            "storage_location": f"previews/{marker}.json",
            "content_hash": ("a" if marker == "alpha" else "b") * 64,
            "marker": marker,
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO domain_events (
                tenant_id, event_id, user_id, device_id, project_id,
                schema_version, event_type, visibility, message, payload, created_at
            ) VALUES (
                :tenant_id, :event_id, :subject_id, 'device', :project_id,
                1, 'task.created', 'user', 'Task created', '{}', now()
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "event_id": event_id,
            "subject_id": subject_id,
            "project_id": project_id,
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO memory_search_documents (
                tenant_id, id, source_kind, source_id, source_revision,
                namespace, project_id, language, normalized_text, content_hash,
                source_cursor, projection_generation, updated_at
            ) VALUES (
                :tenant_id, :document_id, 'domain_event', :event_id, 0,
                'project_canonical', :project_id, 'und', :memory_text, :content_hash,
                1, 1, now()
            )
            """
        ),
        {
            "tenant_id": tenant_id,
            "document_id": memory_document_id,
            "event_id": event_id,
            "project_id": project_id,
            "memory_text": memory_text,
            "content_hash": "0" * 64,
        },
    )
