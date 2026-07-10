from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fairy_core.domain.errors import MemoryConflictError
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import ScopeContract
from fairy_core.memory.models import MemoryNamespace
from fairy_core.memory.retrieval_models import (
    MemorySearchDocument,
    MemorySourceKind,
    ProjectionState,
)
from fairy_core.memory.search_queries import build_postgres_search_statement
from fairy_core.memory.search_sqlalchemy import (
    SqlAlchemyMemoryProjectionWriter,
    SqlAlchemyMemorySearchIndex,
)
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.storage.sqlalchemy import SqlAlchemyStateStore
from tests.memory_support import build_memory_domain_context


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    value = create_sqlite_core_engine(tmp_path / "search.db")
    yield value
    value.dispose()


def _seed_scope(engine: Engine, tmp_path: Path, name: str, *, tenant_id: str = "tenant-a"):
    project, base, conversation, task, draft, scope = build_memory_domain_context(
        tmp_path,
        name,
    )
    state = SqlAlchemyStateStore(engine, tenant_id=tenant_id)
    state.save_project(project)
    state.save_version(base)
    state.save_conversation(conversation)
    state.save_task(task, idempotency_key=f"task:{name}")
    state.save_version(draft)
    return project, base, conversation, task, draft, scope


def _document(
    scope: ScopeContract,
    *,
    text_value: str,
    source_kind: MemorySourceKind = MemorySourceKind.OBSERVATION,
    source_id=None,
    source_revision: int | None = None,
    namespace: MemoryNamespace | None = MemoryNamespace.CONVERSATION_DRAFT,
    generation: int = 1,
    cursor: int = 1,
) -> MemorySearchDocument:
    return MemorySearchDocument.create(
        source_kind=source_kind,
        source_id=source_id or new_id(),
        source_revision=source_revision,
        namespace=namespace,
        project_id=scope.project_id,
        conversation_id=scope.conversation_id,
        task_id=scope.task_id,
        version_id=scope.target_version_id,
        language="und",
        normalized_text=text_value,
        source_cursor=cursor,
        projection_generation=generation,
    )


def test_sqlite_fts_search_is_scoped_and_generation_bound(
    engine: Engine,
    tmp_path: Path,
) -> None:
    *_current, scope = _seed_scope(engine, tmp_path, "current")
    *_other, other_scope = _seed_scope(engine, tmp_path, "other")
    writer = SqlAlchemyMemoryProjectionWriter(engine, tenant_id="tenant-a")
    search = SqlAlchemyMemorySearchIndex(engine, tenant_id="tenant-a")
    canonical = _document(
        scope,
        text_value="React Aria accessibility framework",
        source_kind=MemorySourceKind.CLAIM_REVISION,
        source_revision=1,
        namespace=MemoryNamespace.PROJECT_CANONICAL,
        cursor=2,
    )
    draft = _document(scope, text_value="React draft implementation notes", cursor=3)
    unrelated = _document(other_scope, text_value="React secret from another conversation")
    generation_two = _document(
        scope,
        text_value="React generation two",
        generation=2,
        cursor=4,
    )

    writer.upsert_documents((canonical, draft, unrelated, generation_two))
    writer.advance_checkpoint(generation=1, source_watermark_cursor=3)

    hits = search.search(scope=scope, query="react", generation=1, limit=20)

    assert [hit.document.source_id for hit in hits] == [draft.source_id, canonical.source_id]
    assert unrelated.source_id not in {hit.document.source_id for hit in hits}
    assert generation_two.source_id not in {hit.document.source_id for hit in hits}


def test_projection_upsert_updates_one_source_and_external_content_index(
    engine: Engine,
    tmp_path: Path,
) -> None:
    *_context, scope = _seed_scope(engine, tmp_path, "upsert")
    writer = SqlAlchemyMemoryProjectionWriter(engine, tenant_id="tenant-a")
    search = SqlAlchemyMemorySearchIndex(engine, tenant_id="tenant-a")
    source_id = new_id()
    original = _document(scope, text_value="old framework", source_id=source_id)
    replacement = _document(
        scope,
        text_value="accessibility replacement",
        source_id=source_id,
        cursor=2,
    )

    writer.upsert_documents((original, replacement))
    writer.advance_checkpoint(generation=1, source_watermark_cursor=2)

    assert search.search(scope=scope, query="old", generation=1, limit=10) == ()
    hits = search.search(scope=scope, query="access", generation=1, limit=10)
    assert [hit.document.source_id for hit in hits] == [source_id]
    with engine.connect() as connection:
        row_count = connection.execute(
            text("SELECT count(*) FROM memory_search_documents WHERE tenant_id = 'tenant-a'")
        ).scalar_one()
        fts_sql = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'memory_search_documents_fts'")
        ).scalar_one()
    assert row_count == 1
    assert "content='memory_search_documents'" in fts_sql


def test_sqlite_external_content_row_identity_survives_vacuum(
    engine: Engine,
    tmp_path: Path,
) -> None:
    *_context, scope = _seed_scope(engine, tmp_path, "vacuum")
    writer = SqlAlchemyMemoryProjectionWriter(engine, tenant_id="tenant-a")
    search = SqlAlchemyMemorySearchIndex(engine, tenant_id="tenant-a")
    removed = _document(scope, text_value="remove before vacuum")
    retained = _document(scope, text_value="stable after vacuum", cursor=2)
    writer.upsert_documents((removed, retained))
    writer.remove_source(
        source_kind=removed.source_kind,
        source_id=removed.source_id,
    )

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("VACUUM")
        columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(memory_search_documents)")
        }
        fts_sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name = 'memory_search_documents_fts'"
        ).scalar_one()

    hits = search.search(scope=scope, query="stable", generation=1, limit=10)
    assert "fts_rowid" in columns
    assert "content_rowid='fts_rowid'" in fts_sql
    assert [hit.document.source_id for hit in hits] == [retained.source_id]


def test_projection_upsert_rejects_same_cursor_conflict_and_ignores_stale_source(
    engine: Engine,
    tmp_path: Path,
) -> None:
    *_context, scope = _seed_scope(engine, tmp_path, "monotonic")
    source_id = new_id()
    writer = SqlAlchemyMemoryProjectionWriter(engine, tenant_id="tenant-a")
    search = SqlAlchemyMemorySearchIndex(engine, tenant_id="tenant-a")
    current = _document(
        scope,
        text_value="current projection",
        source_id=source_id,
        cursor=3,
    )
    stale = _document(
        scope,
        text_value="stale projection",
        source_id=source_id,
        cursor=2,
    )
    conflicting = _document(
        scope,
        text_value="conflicting projection",
        source_id=source_id,
        cursor=3,
    )

    writer.upsert_documents((current, stale))

    assert len(search.search(scope=scope, query="current", generation=1, limit=10)) == 1
    assert search.search(scope=scope, query="stale", generation=1, limit=10) == ()
    with pytest.raises(MemoryConflictError, match="cursor"):
        writer.upsert_documents((conflicting,))
    assert len(search.search(scope=scope, query="current", generation=1, limit=10)) == 1


def test_search_isolates_identical_scope_and_source_ids_by_tenant(
    engine: Engine,
    tmp_path: Path,
) -> None:
    project, base, conversation, task, draft, scope = _seed_scope(
        engine,
        tmp_path,
        "tenant-isolation",
    )
    state_b = SqlAlchemyStateStore(engine, tenant_id="tenant-b")
    state_b.save_project(project)
    state_b.save_version(base)
    state_b.save_conversation(conversation)
    state_b.save_task(task, idempotency_key="task:tenant-isolation")
    state_b.save_version(draft)
    source_id = new_id()
    tenant_a_document = _document(
        scope,
        text_value="alpha tenant memory",
        source_id=source_id,
    )
    tenant_b_document = _document(
        scope,
        text_value="beta tenant memory",
        source_id=source_id,
    )
    writer_a = SqlAlchemyMemoryProjectionWriter(engine, tenant_id="tenant-a")
    writer_b = SqlAlchemyMemoryProjectionWriter(engine, tenant_id="tenant-b")
    search_a = SqlAlchemyMemorySearchIndex(engine, tenant_id="tenant-a")
    search_b = SqlAlchemyMemorySearchIndex(engine, tenant_id="tenant-b")

    writer_a.upsert_documents((tenant_a_document,))
    writer_b.upsert_documents((tenant_b_document,))

    assert len(search_a.search(scope=scope, query="alpha", generation=1, limit=10)) == 1
    assert search_a.search(scope=scope, query="beta", generation=1, limit=10) == ()
    assert len(search_b.search(scope=scope, query="beta", generation=1, limit=10)) == 1
    assert search_b.search(scope=scope, query="alpha", generation=1, limit=10) == ()


@pytest.mark.parametrize("query", ['" OR *', "' ; DROP TABLE memory_search_documents; --", "***"])
def test_search_treats_operators_and_punctuation_as_text(
    engine: Engine,
    tmp_path: Path,
    query: str,
) -> None:
    *_context, scope = _seed_scope(engine, tmp_path, "malicious")
    writer = SqlAlchemyMemoryProjectionWriter(engine, tenant_id="tenant-a")
    search = SqlAlchemyMemorySearchIndex(engine, tenant_id="tenant-a")
    document = _document(scope, text_value="React accessibility")
    writer.upsert_documents((document,))

    assert search.search(scope=scope, query=query, generation=1, limit=10) == ()
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM memory_search_documents")).scalar_one()
            == 1
        )


def test_projection_health_reports_unavailable_stale_and_ready(
    engine: Engine,
    tmp_path: Path,
) -> None:
    search = SqlAlchemyMemorySearchIndex(engine, tenant_id="tenant-a")
    writer = SqlAlchemyMemoryProjectionWriter(engine, tenant_id="tenant-a")

    unavailable = search.health(generation=1, source_watermark_cursor=4)
    writer.advance_checkpoint(generation=1, source_watermark_cursor=4)
    ready = search.health(generation=1, source_watermark_cursor=4)
    stale = search.health(generation=1, source_watermark_cursor=5)
    monotonic = writer.advance_checkpoint(generation=1, source_watermark_cursor=3)

    assert unavailable.state is ProjectionState.UNAVAILABLE
    assert ready.state is ProjectionState.READY
    assert ready.projected_watermark_cursor == 4
    assert stale.state is ProjectionState.STALE
    assert stale.projected_watermark_cursor == 4
    assert monotonic.projected_watermark_cursor == 4


def test_projection_health_reports_fts_unavailable(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE memory_search_documents_fts")
    search = SqlAlchemyMemorySearchIndex(engine, tenant_id="tenant-a")

    health = search.health(generation=1, source_watermark_cursor=0)

    assert health.state is ProjectionState.UNAVAILABLE
    assert health.last_error_code == "MEMORY_PROJECTION_UNAVAILABLE"


def test_remove_source_removes_every_projection_generation(
    engine: Engine,
    tmp_path: Path,
) -> None:
    *_context, scope = _seed_scope(engine, tmp_path, "remove")
    source_id = new_id()
    writer = SqlAlchemyMemoryProjectionWriter(engine, tenant_id="tenant-a")
    search = SqlAlchemyMemorySearchIndex(engine, tenant_id="tenant-a")
    writer.upsert_documents(
        (
            _document(scope, text_value="remove me", source_id=source_id),
            _document(
                scope,
                text_value="remove me generation two",
                source_id=source_id,
                generation=2,
            ),
        )
    )

    writer.remove_source(source_kind=MemorySourceKind.OBSERVATION, source_id=source_id)

    assert search.search(scope=scope, query="remove", generation=1, limit=10) == ()
    assert search.search(scope=scope, query="remove", generation=2, limit=10) == ()


def test_postgres_statement_keeps_user_query_in_bound_parameters(
    engine: Engine,
    tmp_path: Path,
) -> None:
    *_context, scope = _seed_scope(engine, tmp_path, "postgres-sql")

    statement = build_postgres_search_statement(
        scope=scope,
        query='react" OR *',
        generation=1,
        limit=10,
        tenant_id="tenant-a",
    )
    compiled = statement.compile()
    sql = str(compiled).lower()

    assert "websearch_to_tsquery('simple', :query)" in sql
    assert "to_tsquery('simple', :prefix_query)" in sql
    assert "memory_search_documents.tenant_id" not in sql
    assert 'react" or *' not in sql
    assert compiled.params["query"] == '"react" "or"'
    assert compiled.params["prefix_query"] == "react:* & or:*"
    assert compiled.params["tenant_id"] == "tenant-a"


def test_search_rejects_queries_above_public_bound(engine: Engine, tmp_path: Path) -> None:
    *_context, scope = _seed_scope(engine, tmp_path, "query-bound")
    search = SqlAlchemyMemorySearchIndex(engine, tenant_id="tenant-a")

    with pytest.raises(ValueError, match="10,000"):
        search.search(scope=scope, query="x" * 10_001, generation=1, limit=10)
