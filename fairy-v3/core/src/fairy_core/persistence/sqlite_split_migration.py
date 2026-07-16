from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from sqlalchemy import Table, func, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine

from fairy_core.commanding.schema import (
    command_metadata,
    command_runs,
    domain_events,
    event_ledgers,
    task_event_sequences,
)
from fairy_core.commanding.sqlite_migrations import (
    migrate_pre_tenant_ledger,
    prepare_pre_tenant_schema,
)
from fairy_core.storage.schema import (
    approvals,
    changesets,
    checkpoints,
    conversations,
    projects,
    state_metadata,
    tasks,
    versions,
    workspaces,
)
from fairy_core.storage.sqlite_engine import create_sqlite_engine
from fairy_core.storage.sqlite_migrations import (
    migrate_assistant_model_routing,
    migrate_checkpoint_evidence,
    migrate_pre_tenant_schema,
    migrate_runtime_graph,
    migrate_runtime_workspace_binding,
    migrate_task_snapshot_binding,
    migrate_workspace_identity,
)

_STATE_IMPORT_REVISION = "20260710_split_state_db_v1"
_LEDGER_IMPORT_REVISION = "20260710_split_ledger_db_v1"
_STATE_TABLES = (
    workspaces,
    projects,
    conversations,
    versions,
    tasks,
    changesets,
    approvals,
    checkpoints,
)


def import_split_sqlite_databases(
    destination: Engine,
    *,
    destination_path: Path,
    state_path: Path | None,
    ledger_path: Path | None,
    tenant_id: str,
) -> None:
    _import_database(
        destination,
        destination_path=destination_path,
        source_path=state_path,
        tenant_id=tenant_id,
        revision=_STATE_IMPORT_REVISION,
        normalize=_normalize_state_database,
        copy_rows=_copy_state_rows,
    )
    _import_database(
        destination,
        destination_path=destination_path,
        source_path=ledger_path,
        tenant_id=tenant_id,
        revision=_LEDGER_IMPORT_REVISION,
        normalize=_normalize_ledger_database,
        copy_rows=_copy_ledger_rows,
    )


def _import_database(
    destination: Engine,
    *,
    destination_path: Path,
    source_path: Path | None,
    tenant_id: str,
    revision: str,
    normalize: Callable[[Engine, str], None],
    copy_rows: Callable[[Connection, Connection, str], None],
) -> None:
    if source_path is None:
        return
    if source_path.resolve(strict=False) == destination_path.resolve(strict=False):
        return
    importing_path = Path(f"{source_path}.fairy-v3-importing")
    archive_path = Path(f"{source_path}.fairy-v3-migrated")
    with destination.begin() as connection:
        migration_applied = _migration_applied(connection, revision)
    if migration_applied:
        if source_path.exists():
            raise RuntimeError(f"legacy database reappeared after migration: {source_path}")
        if importing_path.exists():
            _archive_claim(importing_path, archive_path)
        return

    claimed_path, archive_after_import = _claim_legacy_database(
        source_path,
        importing_path=importing_path,
        archive_path=archive_path,
    )
    if claimed_path is None:
        return

    with (
        _normalized_copy(
            claimed_path,
            working_directory=destination_path.parent,
            tenant_id=tenant_id,
            normalize=normalize,
        ) as source,
        source.connect() as source_connection,
        destination.begin() as destination_connection,
    ):
        if _migration_applied(destination_connection, revision):
            return
        copy_rows(source_connection, destination_connection, tenant_id)
        destination_connection.execute(
            text(
                """
                INSERT INTO core_local_migrations (revision, applied_at)
                VALUES (:revision, :applied_at)
                ON CONFLICT(revision) DO NOTHING
                """
            ),
            {"revision": revision, "applied_at": datetime.now(UTC).isoformat()},
        )
    if archive_after_import:
        _archive_claim(claimed_path, archive_path)


def _claim_legacy_database(
    source_path: Path,
    *,
    importing_path: Path,
    archive_path: Path,
) -> tuple[Path | None, bool]:
    if importing_path.exists():
        if source_path.exists():
            raise RuntimeError(f"both live and interrupted legacy databases exist: {source_path}")
        return importing_path, True
    if source_path.exists():
        if archive_path.exists():
            raise RuntimeError(f"both live and archived legacy databases exist: {source_path}")
        _checkpoint_legacy_database(source_path)
        try:
            source_path.replace(importing_path)
        except OSError as error:
            raise RuntimeError(
                f"legacy database is still in use and cannot be migrated: {source_path}"
            ) from error
        return importing_path, True
    if archive_path.exists():
        return archive_path, False
    return None, False


def _checkpoint_legacy_database(path: Path) -> None:
    try:
        with closing(sqlite3.connect(path, timeout=0)) as connection:
            connection.execute("PRAGMA busy_timeout = 0")
            busy, _log_frames, _checkpointed_frames = connection.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone()
    except sqlite3.Error as error:
        raise RuntimeError(f"legacy database is still in use: {path}") from error
    if busy:
        raise RuntimeError(f"legacy database is still in use: {path}")


def _archive_claim(importing_path: Path, archive_path: Path) -> None:
    if archive_path.exists():
        raise RuntimeError(f"legacy migration archive already exists: {archive_path}")
    importing_path.replace(archive_path)


@contextmanager
def _normalized_copy(
    source_path: Path,
    *,
    working_directory: Path,
    tenant_id: str,
    normalize: Callable[[Engine, str], None],
) -> Iterator[Engine]:
    with TemporaryDirectory(prefix="fairy-v3-migration-", dir=working_directory) as directory:
        copied_path = Path(directory) / "legacy.db"
        source_uri = f"{source_path.resolve(strict=True).as_uri()}?mode=ro"
        with (
            closing(sqlite3.connect(source_uri, uri=True, timeout=5)) as source_connection,
            closing(sqlite3.connect(copied_path, timeout=5)) as copied_connection,
        ):
            source_connection.backup(copied_connection)
        engine = create_sqlite_engine(copied_path)
        try:
            normalize(engine, tenant_id)
            yield engine
        finally:
            engine.dispose()


def _normalize_state_database(engine: Engine, tenant_id: str) -> None:
    state_metadata.create_all(engine)
    migrate_assistant_model_routing(engine)
    migrate_workspace_identity(engine)
    migrate_runtime_workspace_binding(engine)
    migrate_runtime_graph(engine)
    migrate_task_snapshot_binding(engine)
    migrate_checkpoint_evidence(engine)
    migrate_pre_tenant_schema(engine, tenant_id=tenant_id)


def _normalize_ledger_database(engine: Engine, tenant_id: str) -> None:
    prepare_pre_tenant_schema(engine)
    command_metadata.create_all(engine)
    migrate_pre_tenant_ledger(engine, tenant_id=tenant_id)


def _copy_state_rows(
    source: Connection,
    destination: Connection,
    _tenant_id: str,
) -> None:
    for table in _STATE_TABLES:
        rows = source.execute(select(table)).mappings()
        for row in rows:
            _insert_or_verify(destination, table, dict(row))


def _copy_ledger_rows(
    source: Connection,
    destination: Connection,
    _tenant_id: str,
) -> None:
    ledger_rows = source.execute(select(event_ledgers)).mappings()
    for row in ledger_rows:
        _insert_or_verify(destination, event_ledgers, dict(row))

    run_rows = source.execute(select(command_runs)).mappings()
    for row in run_rows:
        _insert_or_verify(destination, command_runs, dict(row))

    event_rows = source.execute(select(domain_events).order_by(domain_events.c.cursor)).mappings()
    for row in event_rows:
        values = dict(row)
        result = destination.execute(
            sqlite_insert(domain_events).values(**values).on_conflict_do_nothing()
        )
        if result.rowcount == 0:
            existing = (
                destination.execute(
                    select(domain_events).where(
                        domain_events.c.tenant_id == values["tenant_id"],
                        domain_events.c.event_id == values["event_id"],
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is None or not _rows_equal(existing, values):
                raise RuntimeError("legacy domain event conflicts with the unified event sequence")

    sequence_rows = source.execute(select(task_event_sequences)).mappings()
    for row in sequence_rows:
        statement = sqlite_insert(task_event_sequences).values(**dict(row))
        destination.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    task_event_sequences.c.tenant_id,
                    task_event_sequences.c.task_id,
                ],
                set_={
                    "last_sequence": func.max(
                        task_event_sequences.c.last_sequence,
                        statement.excluded.last_sequence,
                    )
                },
            )
        )


def _insert_or_verify(destination: Connection, table: Table, values: dict[str, Any]) -> None:
    result = destination.execute(sqlite_insert(table).values(**values).on_conflict_do_nothing())
    if result.rowcount != 0:
        return
    identity = [table.c[column.name] == values[column.name] for column in table.primary_key.columns]
    existing = destination.execute(select(table).where(*identity)).mappings().one_or_none()
    if existing is None or not _rows_equal(existing, values):
        raise RuntimeError(f"legacy row conflicts with unified table {table.name}")


def _rows_equal(
    existing: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    ignored: tuple[str, ...] = (),
) -> bool:
    return all(existing[key] == value for key, value in expected.items() if key not in ignored)


def _migration_applied(connection: Connection, revision: str) -> bool:
    return (
        connection.execute(
            text("SELECT 1 FROM core_local_migrations WHERE revision = :revision"),
            {"revision": revision},
        ).first()
        is not None
    )
