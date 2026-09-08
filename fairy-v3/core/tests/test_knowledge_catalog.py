from __future__ import annotations

import hashlib
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect

from fairy_core.application.core import CoreApplication
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.knowledge import KnowledgeItemListInput, KnowledgeProjectInput
from fairy_core.domain.models import ProjectResidency
from fairy_core.knowledge.application import ProjectKnowledgeApplication
from fairy_core.knowledge.models import (
    KnowledgeCollection,
    KnowledgeIngestItem,
    KnowledgeSource,
    KnowledgeSourceKind,
)
from fairy_core.knowledge.sqlite_migrations import migrate_knowledge_catalog_length
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner


def _seed(factory, root, name):
    registry = build_default_registry()
    core = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(root),
        registry=registry,
        policy=PolicyEngine(registry),
    )
    project = core.create_project(name=name, residency=ProjectResidency.LOCAL_ONLY).project
    source = KnowledgeSource.create(
        source_id=uuid4(),
        project_id=project.id,
        kind=KnowledgeSourceKind.OBSIDIAN,
        device_id="test",
        display_name=name,
        display_path=name,
    )
    collection = KnowledgeCollection.create(
        source_id=source.id,
        project_id=project.id,
        read_scope="whole_vault",
        allowed_directories=(),
        managed_directory="Fairy",
    )
    with factory() as unit:
        unit.knowledge.save_source(source, collection)
        unit.commit()
    return project, source


def _entry(path, content, links=()):
    return KnowledgeIngestItem(
        relative_path=path,
        title=path.removesuffix(".md"),
        kind="markdown",
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        links=links,
        frontmatter={"private": "not needed by directory"},
        provenance={},
    )


def _sync(factory, source, entries, cursor):
    with factory() as unit:
        delta = unit.knowledge.synchronize(
            source=replace(source, sync_cursor=cursor),
            entries=entries,
        )
        unit.commit()
    return delta


def test_catalog_reads_only_metadata_and_preserves_scope_links_and_watermark(tmp_path):
    path = tmp_path / "catalog.db"
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    other = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="other")
    first, first_source = _seed(factory, tmp_path / "first", "First")
    second, second_source = _seed(factory, tmp_path / "second", "Second")
    foreign, foreign_source = _seed(other, tmp_path / "foreign", "Foreign")
    contents = ("你好😀\x00" * 200_000, "# Target")
    _sync(
        factory,
        first_source,
        (
            _entry("Large.md", contents[0], ("Target",)),
            _entry("Target.md", contents[1]),
        ),
        1,
    )
    _sync(factory, second_source, (_entry("Second.md", "private"),), 1)
    _sync(other, foreign_source, (_entry("Foreign.md", "other tenant"),), 1)
    engine.dispose()
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    selected = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if (
            statement.lstrip().upper().startswith("SELECT")
            and "core_knowledge_revisions" in statement
        ):
            selected.append({column[0] for column in _cursor.description})

    event.listen(engine, "after_cursor_execute", capture)
    try:
        app = ProjectKnowledgeApplication(factory)
        request = KnowledgeProjectInput(project_id=first.id)
        overview = app.overview(request)
        items = app.list_items(KnowledgeItemListInput(project_id=first.id))
        graph = app.graph(request)
        assert overview.note_count == 2
        assert {item.title for item in items.items} == {"Large", "Target"}
        assert {item.byte_length for item in items.items} == {
            len(text.encode()) for text in contents
        }
        assert overview.watermark == items.watermark == graph.watermark
        assert any(edge.relation.value == "references" for edge in graph.edges)
        assert selected and all(
            not fields & {"content", "frontmatter", "provenance"} for fields in selected
        )
        assert {
            item.title
            for item in app.list_items(KnowledgeItemListInput(project_id=second.id)).items
        } == {"Second"}
        with factory() as unit:
            assert unit.knowledge.current_revision_metadata(foreign.id) == ()
        with SqlAlchemyUnitOfWorkFactory(engine, tenant_id="other")() as unit:
            assert unit.knowledge.current_revision_metadata(first.id) == ()
            foreign_metadata = unit.knowledge.current_revision_metadata(foreign.id)
            assert [item.title for item in foreign_metadata] == ["Foreign"]
        old_watermark = items.watermark
        _sync(factory, first_source, (_entry("Target.md", "changed😀"),), 2)
        latest = app.list_items(KnowledgeItemListInput(project_id=first.id))
        assert [item.title for item in latest.items] == ["Target"]
        assert latest.items[0].byte_length == len("changed😀".encode())
        assert latest.watermark != old_watermark
    finally:
        event.remove(engine, "after_cursor_execute", capture)
        engine.dispose()


def test_catalog_byte_length_migrates_existing_unicode_rows_once(tmp_path):
    path = tmp_path / "old.db"
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    _project, source = _seed(factory, tmp_path / "managed", "Old")
    delta = _sync(factory, source, (_entry("Old.md", "a\x00你好😀"),), 1)
    with engine.begin() as connection:
        columns = {
            column["name"] for column in inspect(connection).get_columns("core_knowledge_revisions")
        }
        if "byte_length" in columns:
            connection.exec_driver_sql(
                "ALTER TABLE core_knowledge_revisions DROP COLUMN byte_length"
            )
    engine.dispose()
    for _ in range(2):
        engine = create_sqlite_core_engine(path)
        try:
            with engine.connect() as connection:
                columns = {
                    column["name"]
                    for column in inspect(connection).get_columns("core_knowledge_revisions")
                }
                assert "byte_length" in columns
                row = connection.exec_driver_sql(
                    "SELECT byte_length, revision_hash, content FROM core_knowledge_revisions"
                ).one()
                assert row.byte_length == len("a\x00你好😀".encode())
                assert row.revision_hash == delta.created_revisions[0].revision_hash
                assert row.content == "a\x00你好😀"
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        finally:
            engine.dispose()


def test_catalog_migration_failure_does_not_leave_zero_length_metadata(tmp_path):
    engine = create_sqlite_core_engine(tmp_path / "interrupted.db")
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE core_knowledge_revisions DROP COLUMN byte_length")

    def interrupt(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("UPDATE core_knowledge_revisions SET byte_length"):
            raise RuntimeError("Injected migration interruption")

    event.listen(engine, "before_cursor_execute", interrupt)
    try:
        with pytest.raises(RuntimeError, match="Injected"):
            migrate_knowledge_catalog_length(engine)
        with engine.connect() as connection:
            assert "byte_length" not in {
                column["name"]
                for column in inspect(connection).get_columns("core_knowledge_revisions")
            }
    finally:
        event.remove(engine, "before_cursor_execute", interrupt)
        engine.dispose()
