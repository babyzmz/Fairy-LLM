from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, sleep
from uuid import UUID

import pytest
from sqlalchemy import update

from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.assistant.trace_models import TraceStepKind
from fairy_core.commanding.schema import command_runs
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.storage.schema import assistant_turn_work
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider, wait_for_turn
from tests.assistant.test_application import BlockingProvider, _scratch_task, _turn


def _completion_provider() -> ScriptedProvider:
    return ScriptedProvider(
        [
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text="Recovered once"),
                ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="stop"),
            )
        ]
    )


def test_started_turn_recovers_from_an_expired_durable_work_claim(tmp_path: Path) -> None:
    first_provider = _completion_provider()
    first_service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((first_provider,)),
    )
    task = _scratch_task(first_service, "Complete after a Core restart")
    turn = _turn(first_service, task, "turn:durable-worker:restart")
    first_service.close()

    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    ledger = AssistantLedgerApplication(
        unit_of_work_factory=factory,
        scope_resolver=lambda _state, _task: (_ for _ in ()).throw(AssertionError()),
    )
    ledger.enqueue_turn_work(UUID(turn["id"]))
    claim = ledger.claim_turn_work(
        UUID(turn["id"]),
        worker_id="crashed-core",
        lease_until=datetime.now(UTC) + timedelta(seconds=30),
    )
    assert claim is not None
    with engine.begin() as connection:
        connection.execute(
            update(assistant_turn_work)
            .where(assistant_turn_work.c.turn_id == turn["id"])
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    engine.dispose()

    recovered_provider = _completion_provider()
    recovered_service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((recovered_provider,)),
    )
    try:
        completed = wait_for_turn(recovered_service, turn["id"])
        transcript = recovered_service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"], "limit": 100},
        )["items"]

        assert completed["status"] == "completed"
        assert len(recovered_provider.requests) == 1
        assert [message["role"] for message in transcript] == ["user", "assistant"]
        assert transcript[-1]["content"] == "Recovered once"
        assert recovered_service._assistant_ledger.pending_turn_work_ids() == ()  # type: ignore[attr-defined]
    finally:
        recovered_service.close()


def test_recovered_model_round_reclaims_command_and_resets_partial_projection(
    tmp_path: Path,
) -> None:
    first_service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((_completion_provider(),)),
    )
    task = _scratch_task(first_service, "Replace interrupted model text")
    turn = _turn(first_service, task, "turn:durable-worker:model-reclaim")
    turn_id = UUID(turn["id"])
    _persisted, run = first_service._assistant_application._start_model_round(  # type: ignore[attr-defined]
        turn_id,
        1,
        profile_id="scripted",
    )
    first_service._assistant_application._append_delta(  # type: ignore[attr-defined]
        turn_id=turn_id,
        run=run,
        model_round=1,
        chunk_index=1,
        text="stale partial",
    )
    first_service._assistant_scheduler.close()  # type: ignore[attr-defined]
    first_service._assistant_ledger.enqueue_turn_work(turn_id)  # type: ignore[attr-defined]
    claim = first_service._assistant_ledger.claim_turn_work(  # type: ignore[attr-defined]
        turn_id,
        worker_id="crashed-core",
        lease_until=datetime.now(UTC) + timedelta(seconds=30),
    )
    assert claim is not None
    engine = first_service._unit_of_work_factory._engine  # type: ignore[attr-defined]
    with engine.begin() as connection:
        expired = datetime.now(UTC) - timedelta(seconds=1)
        connection.execute(
            update(assistant_turn_work)
            .where(assistant_turn_work.c.turn_id == turn["id"])
            .values(lease_until=expired)
        )
        connection.execute(
            update(command_runs).where(command_runs.c.id == str(run.id)).values(lease_until=expired)
        )
    first_service.close()

    recovered_provider = _completion_provider()
    recovered_service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((recovered_provider,)),
    )
    try:
        completed = wait_for_turn(recovered_service, turn["id"])
        with recovered_service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            events = unit_of_work.commands.events_for_run(run.id)
            steps = unit_of_work.assistant.list_trace_steps(turn_id)
            attempts = unit_of_work.assistant.list_provider_attempts(turn_id)

        assert completed["status"] == "completed"
        assert len(recovered_provider.requests) == 1
        assert [event.event_type for event in events].count("command.reclaimed") == 1
        assert [event.event_type for event in events].count(
            "assistant.message.projection_reset"
        ) == 1
        assert len([step for step in steps if step.kind is TraceStepKind.MODEL]) == 1
        assert len(attempts) == 1
    finally:
        recovered_service.close()


def test_active_turn_survives_graceful_core_close(tmp_path: Path) -> None:
    blocking_provider = BlockingProvider()
    first_service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((blocking_provider,)),
    )
    task = _scratch_task(first_service, "Continue after the local Core closes")
    turn = _turn(first_service, task, "turn:durable-worker:graceful-close")
    first_service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
    assert blocking_provider.started.wait(timeout=2)

    first_service.close()

    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    ledger = AssistantLedgerApplication(
        unit_of_work_factory=factory,
        scope_resolver=lambda _state, _task: (_ for _ in ()).throw(AssertionError()),
    )
    interrupted = ledger.get_turn(UUID(turn["id"]))
    assert interrupted.status.value == "running"
    assert ledger.pending_turn_work_ids() == (UUID(turn["id"]),)
    engine.dispose()

    recovered_provider = _completion_provider()
    recovered_service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((recovered_provider,)),
    )
    try:
        completed = wait_for_turn(recovered_service, turn["id"])
        assert completed["status"] == "completed"
        assert len(recovered_provider.requests) == 1
    finally:
        recovered_service.close()


def test_lost_heartbeat_retries_without_consuming_turn_work(tmp_path: Path) -> None:
    provider = BlockingProvider()
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        task = _scratch_task(service, "Retry after losing the worker lease")
        turn = _turn(service, task, "turn:durable-worker:heartbeat")
        turn_id = UUID(turn["id"])
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        assert provider.started.wait(timeout=2)

        scheduler = service._assistant_scheduler  # type: ignore[attr-defined]
        deadline = monotonic() + 2
        active = None
        while monotonic() < deadline:
            with scheduler._lock:  # type: ignore[attr-defined]
                active = scheduler._active.get(turn_id)  # type: ignore[attr-defined]
            if active is not None:
                break
            sleep(0.01)
        assert active is not None

        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            model_steps = [
                step
                for step in unit_of_work.assistant.list_trace_steps(turn_id)
                if step.kind is TraceStepKind.MODEL and step.command_run_id is not None
            ]
            assert len(model_steps) == 1
            initial_command = unit_of_work.commands.get_run(model_steps[0].command_run_id)
        assert initial_command is not None
        assert initial_command.lease_until is not None
        assert initial_command.lease_until < active.claim.lease_until

        scheduler._last_heartbeat = 0  # type: ignore[attr-defined]
        scheduler._heartbeat_if_due()  # type: ignore[attr-defined]
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            renewed_command = unit_of_work.commands.get_run(initial_command.id)
        assert renewed_command is not None
        assert renewed_command.lease_until is not None
        assert renewed_command.lease_until > initial_command.lease_until

        assert service._assistant_ledger.abandon_turn_work(active.claim)  # type: ignore[attr-defined]

        scheduler._last_heartbeat = 0  # type: ignore[attr-defined]
        scheduler._heartbeat_if_due()  # type: ignore[attr-defined]
        provider.release.set()

        completed = wait_for_turn(service, turn["id"])
        transcript = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"], "limit": 100},
        )["items"]
        assert completed["status"] == "completed"
        assert len(provider.requests) == 2
        assert [message["role"] for message in transcript] == ["user", "assistant"]
        assert transcript[-1]["content"] == "streamed"
    finally:
        service.close()


def test_coordinator_retries_after_a_transient_claim_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _completion_provider()
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    ledger = service._assistant_ledger  # type: ignore[attr-defined]
    original_claim = ledger.claim_next_turn_work
    attempts = 0

    def flaky_claim(*, worker_id: str, lease_until: datetime):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient database failure")
        return original_claim(worker_id=worker_id, lease_until=lease_until)

    monkeypatch.setattr(ledger, "claim_next_turn_work", flaky_claim)
    try:
        task = _scratch_task(service, "Continue after a transient queue error")
        turn = _turn(service, task, "turn:durable-worker:transient-claim")
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})

        completed = wait_for_turn(service, turn["id"])
        assert completed["status"] == "completed"
        assert attempts >= 2
        assert len(provider.requests) == 1
    finally:
        service.close()
