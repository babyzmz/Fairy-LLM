from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from fairy_core.domain.errors import IdempotencyConflictError
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.storage.schema import assistant_message_submissions
from tests.assistant.test_repository_contract import _seed_task


def test_message_receipts_upgrade_reopen_and_enforce_tenant_foreign_keys(tmp_path: Path) -> None:
    path = tmp_path / "receipts.db"
    engine = create_sqlite_core_engine(path)
    assistant_message_submissions.drop(engine)
    engine.dispose()
    engine = create_sqlite_core_engine(path)
    assert "core_assistant_message_submissions" in inspect(engine).get_table_names()
    conversations = {}
    try:
        for tenant, digest in (("a", "a" * 64), ("b", "b" * 64)):
            factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant)
            task, _scope = _seed_task(factory, label=tenant)
            conversations[tenant] = task.conversation_id
            with factory() as unit:
                unit.assistant.reserve_message_submission(
                    key_digest="c" * 64, request_digest=digest,
                    conversation_id=task.conversation_id,
                )
                unit.commit()
    finally:
        engine.dispose()
    engine = create_sqlite_core_engine(path)
    try:
        for tenant in ("b", "a"):
            factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant)
            with factory() as unit:
                assert unit.assistant.message_submission_digest("c" * 64) == tenant * 64
                unit.assistant.reserve_message_submission(
                    key_digest="c" * 64, request_digest=tenant * 64,
                    conversation_id=conversations[tenant],
                )
                unit.commit()
            with factory() as unit, pytest.raises(IdempotencyConflictError):
                unit.assistant.reserve_message_submission(
                    key_digest="c" * 64, request_digest="d" * 64,
                    conversation_id=conversations[tenant],
                )
            with factory() as unit, pytest.raises(IntegrityError):
                unit.assistant.reserve_message_submission(
                    key_digest="e" * 64, request_digest="f" * 64,
                    conversation_id=conversations["b" if tenant == "a" else "a"],
                )
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    finally:
        engine.dispose()
