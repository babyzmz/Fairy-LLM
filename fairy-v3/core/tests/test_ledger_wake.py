from __future__ import annotations

from fairy_core.commanding.models import EventVisibility
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory


def test_ledger_wake_is_after_commit_not_rollback_and_is_tenant_scoped(tmp_path):
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    first = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="first")
    second = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="second")
    try:
        assert first.ledger_signal.version == second.ledger_signal.version == 0
        with first() as unit:
            unit.commands.append_domain_event(
                event_type="test.committed",
                visibility=EventVisibility.USER,
                message="Public event",
                payload={},
                actor="user",
            )
            assert first.ledger_signal.version == 0
            unit.commit()
        assert first.ledger_signal.version == 1
        assert second.ledger_signal.version == 0
        with first() as unit:
            unit.commands.append_domain_event(
                event_type="test.rolled_back",
                visibility=EventVisibility.USER,
                message="Not committed",
                payload={},
                actor="user",
            )
        assert first.ledger_signal.version == 1
        with first() as unit:
            unit.commands.events_after(cursor=0)
            unit.commit()
        assert first.ledger_signal.version == 1
        with second() as unit:
            unit.commands.append_domain_event(
                event_type="test.second",
                visibility=EventVisibility.USER,
                message="Other tenant",
                payload={},
                actor="user",
            )
            unit.commit()
        assert first.ledger_signal.version == second.ledger_signal.version == 1
    finally:
        engine.dispose()
