from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from fairy_core.domain.errors import IdempotencyConflictError
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.storage.schema import assistant_message_cancellations
from tests.assistant.test_repository_contract import _seed_task


def test_cancellation_receipts_upgrade_reopen_and_isolate_tenants(tmp_path: Path):
    path = tmp_path / "cancellation.db"
    engine = create_sqlite_core_engine(path)
    assistant_message_cancellations.drop(engine)
    engine.dispose()
    engine = create_sqlite_core_engine(path)
    assert "core_assistant_message_cancellations" in inspect(engine).get_table_names()
    conversations = {}
    try:
        for tenant in ("a", "b"):
            factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant)
            task, _ = _seed_task(factory, label=tenant)
            conversations[tenant] = task.conversation_id
            with factory() as unit:
                assert not unit.assistant.message_cancellation_requested("a" * 64, None)
                unit.assistant.request_message_cancellation("a" * 64, task.conversation_id)
                unit.assistant.request_message_cancellation("b" * 64, None)
                unit.commit()
    finally:
        engine.dispose()
    engine = create_sqlite_core_engine(path)
    try:
        for tenant in ("b", "a"):
            factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant)
            with factory() as unit:
                for key in ("a" * 64, "b" * 64):
                    assert unit.assistant.message_cancellation_requested(key, conversations[tenant])
            with factory() as unit, pytest.raises(IdempotencyConflictError):
                unit.assistant.request_message_cancellation(
                    "a" * 64, conversations["b" if tenant == "a" else "a"],
                )
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        engine.dispose()
