from __future__ import annotations

import io
import json
from threading import Condition, Event

import pytest

from fairy_core.commanding.models import EventVisibility
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.transports.stdio import build_local_dispatcher
from fairy_core.transports.stdio_dispatch import StdioRequestDispatcher
from fairy_core.transports.stdio_events import StdioEventWatches


def test_two_watchers_replay_their_own_cursor_and_unwatch_does_not_recreate(tmp_path):
    dispatcher = build_local_dispatcher(tmp_path)
    messages = []
    changed = Condition()

    def emit(value):
        with changed:
            messages.append(value)
            changed.notify_all()

    watches = StdioEventWatches(dispatcher, emit)
    try:
        state = dispatcher.dispatch({"id": 1, "method": "events.state", "params": {}})["result"]
        for key in ("first", "second"):
            watches.watch({"subscription_id": key, "cursor": state["latest_cursor"]})
        with dispatcher._service._unit_of_work_factory() as unit:
            event = unit.commands.append_domain_event(
                event_type="test.committed",
                visibility=EventVisibility.USER,
                message="Watch test",
                payload={},
                actor="user",
            )
            unit.commit()
        with changed:
            assert changed.wait_for(
                lambda: (
                    len(
                        [
                            message
                            for message in messages
                            if any(
                                item["id"] == str(event.id)
                                for item in message["params"].get("items", [])
                            )
                        ]
                    )
                    == 2
                ),
                timeout=2,
            )
        watches.unwatch({"subscription_id": "first"})
        with dispatcher._service._unit_of_work_factory() as unit:
            second = unit.commands.append_domain_event(
                event_type="test.second",
                visibility=EventVisibility.USER,
                message="Second",
                payload={},
                actor="user",
            )
            unit.commit()
        with changed:
            assert changed.wait_for(
                lambda: any(
                    any(item["id"] == str(second.id) for item in message["params"].get("items", []))
                    for message in messages
                ),
                timeout=2,
            )
        recipients = [
            message["params"]["subscription_id"]
            for message in messages
            if any(item["id"] == str(second.id) for item in message["params"].get("items", []))
        ]
        assert recipients == ["second"]
        assert all(message["params"]["ledger_id"] == state["ledger_id"] for message in messages)
    finally:
        watches.close()
        dispatcher.close()


def test_event_watch_requires_explicit_notification_negotiation(tmp_path):
    dispatcher = build_local_dispatcher(tmp_path)
    output = io.StringIO()
    transport = StdioRequestDispatcher(dispatcher, output)
    try:
        transport.dispatch({"id": 1, "method": "transport.negotiate", "params": {}})
        transport.dispatch(
            {"id": 2, "method": "events.watch", "params": {"subscription_id": "a", "cursor": 0}}
        )
        before = [json.loads(line) for line in output.getvalue().splitlines()]
        assert before[0]["result"]["event_notifications"] is False
        assert before[1]["error"]["data"]["error_code"] == "RPC_EVENTS_NOT_NEGOTIATED"
        transport.dispatch(
            {"id": 3, "method": "transport.negotiate", "params": {"event_notifications": True}}
        )
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        assert responses[-1]["result"]["event_notifications"] is True
        transport.dispatch(
            {"id": 4, "method": "events.watch", "params": {"subscription_id": "a", "cursor": 0}}
        )
        transport.dispatch(
            {"id": 5, "method": "events.unwatch", "params": {"subscription_id": "a"}}
        )
    finally:
        transport.close()
        dispatcher.close()
    responses = [json.loads(line) for line in output.getvalue().splitlines() if '"id"' in line]
    assert (
        next(item for item in responses if item.get("id") == 4)["result"]["subscription_id"] == "a"
    )
    assert next(item for item in responses if item.get("id") == 5)["result"]["stopped"] is True


def test_watch_rejects_a_cursor_beyond_the_restored_ledger(tmp_path):
    dispatcher = build_local_dispatcher(tmp_path)
    watches = StdioEventWatches(dispatcher, lambda _: None)
    try:
        with pytest.raises(ValueError, match="resync"):
            watches.watch({"subscription_id": "future", "cursor": 2**62})
    finally:
        watches.close()
        dispatcher.close()


def test_a_lost_local_wake_is_recovered_from_the_persistent_ledger(tmp_path, monkeypatch):
    dispatcher = build_local_dispatcher(tmp_path)
    factory = dispatcher._service._unit_of_work_factory
    signal = factory.ledger_signal
    waiting, received = Event(), Event()
    original_wait = signal.wait

    def wait(after, timeout=5.0):
        waiting.set()
        return original_wait(after, timeout)

    monkeypatch.setattr(signal, "wait", wait)
    event_id = []

    def emit(message):
        if any(item["id"] in event_id for item in message["params"].get("items", [])):
            received.set()

    watches = StdioEventWatches(dispatcher, emit)
    try:
        state = dispatcher.dispatch({"id": 1, "method": "events.state", "params": {}})["result"]
        watches.watch({"subscription_id": "replay", "cursor": state["latest_cursor"]})
        waiting.clear()
        signal.notify()
        assert waiting.wait(2)
        version = signal.version
        # An independent writer deliberately does not signal this Core's factory.
        other = SqlAlchemyUnitOfWorkFactory(factory._engine, tenant_id="local")
        with other() as unit:
            event = unit.commands.append_domain_event(
                event_type="test.lost_hint",
                visibility=EventVisibility.USER,
                message="Durable event",
                payload={},
                actor="user",
            )
            event_id.append(str(event.id))
            unit.commit()
        assert signal.version == version
        assert received.wait(6.5)
    finally:
        watches.close()
        dispatcher.close()
