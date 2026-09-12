"""Back up SQLite consistently and rehearse migrations on a separate copy only."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from fairy_core.persistence.sqlite import create_sqlite_core_engine

TABLES = (
    "core_assistant_messages",
    "core_assistant_turns",
    "core_execution_plans",
    "core_assistant_tool_invocations",
    "core_workflow_runs",
)


def inspect_copy(path):
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        integrity = connection.execute("PRAGMA quick_check").fetchall()
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != [("ok",)] or foreign_keys:
            raise RuntimeError(
                "Database integrity gate failed; source was not modified"
            )
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        counts = {
            table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            for table in TABLES
            if table in names
        }
        engines = []
        if "core_assistant_turns" in names:
            engines = connection.execute(
                "SELECT execution_engine_version, status, count(*) FROM core_assistant_turns "
                "GROUP BY execution_engine_version, status"
            ).fetchall()
        return {"rows": counts, "turn_engine_status_counts": engines}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    backup, candidate = output / "backup.db", output / "migration-probe.db"
    with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as reader:
        with closing(sqlite3.connect(backup)) as writer:
            reader.backup(writer)
    before = inspect_copy(backup)
    with backup.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    with closing(sqlite3.connect(backup)) as reader, closing(sqlite3.connect(candidate)) as writer:
        reader.backup(writer)
    engine = create_sqlite_core_engine(candidate)
    engine.dispose()
    after = inspect_copy(candidate)
    engine = create_sqlite_core_engine(candidate)
    engine.dispose()
    reopened = inspect_copy(candidate)
    if before != after or after != reopened:
        raise RuntimeError("Row/engine identity changed; source was not modified")
    report = {
        "source_modified": False,
        "backup_sha256": digest,
        "migration_and_reopen": "passed",
        **after,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
