from typing import Any

from sqlalchemy import func

from fairy_core.storage.schema import workflow_runs


def timestamp_epoch(connection: Any, column: Any) -> Any:
    if connection.dialect.name == "sqlite":
        return (func.julianday(column) - 2440587.5) * 86400.0
    return func.extract("epoch", column)


def run_deadline_epoch(connection: Any) -> Any:
    return (timestamp_epoch(connection, workflow_runs.c.created_at)
            + workflow_runs.c.max_duration_seconds)
