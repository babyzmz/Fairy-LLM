from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import event, insert

from fairy_core.assistant.context import AssistantContextBuilder
from fairy_core.assistant.models import ImportedMessage, Message, MessageRole, MessageVisibility
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.storage.schema import assistant_messages
from tests.assistant.test_repository_contract import _seed_task


@pytest.mark.parametrize("size", [1_000, 10_000, 100_000])
def test_context_reads_only_recent_history_at_large_sizes(tmp_path, size):
    engine = create_sqlite_core_engine(tmp_path / "history.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    task, _ = _seed_task(factory, label="history")
    now = datetime.now(UTC)
    try:
        with engine.begin() as connection:
            for start in range(1, size + 1, 1_000):
                connection.execute(
                    insert(assistant_messages),
                    [
                        {
                            "tenant_id": "local",
                            "id": str(UUID(int=sequence)),
                            "conversation_id": str(task.conversation_id),
                            "task_id": str(task.id),
                            "turn_id": None,
                            "sequence": sequence,
                            "role": "user",
                            "visibility": "internal" if sequence % 10 == 0 else "user",
                            "content": f"Message {sequence}",
                            "created_at": now,
                        }
                        for sequence in range(start, min(start + 1_000, size + 1))
                    ],
                )
        statements = []

        def capture(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(engine, "after_cursor_execute", capture)
        with factory() as unit:
            result = AssistantContextBuilder._messages(unit.assistant, task.conversation_id)
        expected = [value for value in range(size - 60, size + 1) if value % 10 != 0][-40:]
        assert [message.sequence for message in result] == expected
        assert len(statements) == 2, f"{size} messages caused {len(statements)} SELECTs"
        assert all("LIMIT" in statement.upper() for statement in statements)
    finally:
        engine.dispose()


def test_recent_history_merges_imports_and_isolates_chats_and_tenants_on_reopen(tmp_path):
    path = tmp_path / "mixed.db"
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    left, _ = _seed_task(factory, label="left")
    right, _ = _seed_task(factory, label="right")
    source = Message.create(
        conversation_id=left.conversation_id,
        task_id=left.id,
        turn_id=None,
        sequence=1,
        role=MessageRole.USER,
        visibility=MessageVisibility.USER,
        content="Source",
    )
    imported = ImportedMessage.from_message(
        destination_conversation_id=right.conversation_id,
        sequence=2,
        source=source,
    )
    with factory() as unit:
        unit.assistant.append_message(source)
        unit.assistant.append_imported_message(imported)
        for sequence, visibility in (
            (1, MessageVisibility.DEVELOPER),
            (3, MessageVisibility.USER),
            (4, MessageVisibility.INTERNAL),
        ):
            unit.assistant.append_message(
                Message.create(
                    conversation_id=right.conversation_id,
                    task_id=right.id,
                    turn_id=None,
                    sequence=sequence,
                    role=MessageRole.ASSISTANT,
                    visibility=visibility,
                    content=f"Right {sequence}",
                )
            )
        unit.commit()
    engine.dispose()
    reopened = create_sqlite_core_engine(path)
    try:
        for tenant in ("local", "other"):
            with SqlAlchemyUnitOfWorkFactory(reopened, tenant_id=tenant)() as unit:
                right_result = AssistantContextBuilder._messages(
                    unit.assistant, right.conversation_id
                )
                left_result = AssistantContextBuilder._messages(
                    unit.assistant, left.conversation_id
                )
                if tenant == "local":
                    assert [item.content for item in right_result] == [
                        "Right 1",
                        "Source",
                        "Right 3",
                    ]
                    assert isinstance(right_result[1], ImportedMessage)
                    assert [item.content for item in left_result] == ["Source"]
                else:
                    assert right_result == left_result == ()
    finally:
        reopened.dispose()
