from pathlib import Path
from uuid import UUID

from sqlalchemy import update

from fairy_core.assistant.models import ProviderAttempt, ToolInvocation
from fairy_core.commanding import CommandStatus
from fairy_core.commanding.registry import RiskLevel
from fairy_core.model_catalog.models import ModelEndpointKind
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.providers import ModelExecutionRole, ProviderRegistry
from fairy_core.storage.schema import tasks
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider
from tests.assistant.test_application import _scratch_task, _turn
from tests.assistant.test_repository_contract import _seed_task
from tests.assistant.test_repository_contract import _turn as repository_turn


def test_exclusive_restart_settles_cancelled_model_streams_without_replaying_tools(
    tmp_path: Path,
) -> None:
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((ScriptedProvider([]),)),
    )
    turn_ids = []
    try:
        for index in range(2):
            task = _scratch_task(service, f"Cancelled request {index}")
            result = _turn(service, task, f"cancel-restart:{index}")
            turn_id = UUID(result["id"])
            turn_ids.append(turn_id)
            with service._unit_of_work_factory() as unit:
                turn = unit.assistant.get_turn(turn_id)
                assert turn is not None
                attempt = ProviderAttempt.create(
                    turn=turn, model_round=1, attempt_number=1,
                    profile_id="scripted", model_id="scripted",
                    endpoint_kind=ModelEndpointKind.CHAT,
                    model_role=ModelExecutionRole.PRIMARY,
                )
                attempt.usage = {"input_tokens": 7}
                attempt.usage_cost = "0.01"
                unit.assistant.save_provider_attempt(attempt)
                if index == 1:
                    invocation = ToolInvocation.create(
                        turn=turn, model_round=1, sequence=1,
                        provider_call_id="uncertain-write", tool_name="system.notify",
                        scope_digest=turn.scope_digest,
                        arguments={"title": "Already dispatched", "body": "Unknown outcome"},
                    )
                    persisted_task = unit.state.get_task(turn.task_id)
                    scope = service._application.scope_for_task(unit.state, persisted_task)
                    command = unit.commands.create_run(
                        command_name=invocation.tool_name, actor="assistant", scope=scope,
                        input_payload=invocation.arguments, risk_level=RiskLevel.LOW,
                        idempotency_key="cancel-restart:uncertain-write",
                    )
                    unit.commands.transition(command.id, CommandStatus.QUEUED)
                    invocation.queue(command_run_id=command.id)
                    invocation.start()
                    unit.assistant.save_tool_invocation(invocation)
                unit.commit()
            service.invoke("assistant.turns.cancel", {
                "turn_id": str(turn_id), "expected_cancellation_revision": 0,
            })
            assert service.invoke("assistant.turns.get", {
                "turn_id": str(turn_id),
            })["cancellation_pending"]
        # An ordinary recovery call while this owner is alive must not settle its streams.
        service.recover_interrupted_work()
        assert all(service.invoke("assistant.turns.get", {
            "turn_id": str(value),
        })["cancellation_pending"] for value in turn_ids)
    finally:
        service.close()

    provider = ScriptedProvider([])
    restored = build_local_service(tmp_path, provider_registry=ProviderRegistry((provider,)))
    try:
        first, second = [restored.invoke("assistant.turns.get", {
            "turn_id": str(value),
        }) for value in turn_ids]
        assert first["status"] == "cancelled"
        assert first["cancellation_pending"] is False
        assert second["cancellation_pending"] is True
        assert provider.requests == []
        with restored._unit_of_work_factory() as unit:
            for value in turn_ids:
                (attempt,) = unit.assistant.list_provider_attempts(value)
                assert attempt.status.value == "failed"
                assert attempt.error_category.value == "unknown"
                assert attempt.usage == {"input_tokens": 7}
                assert attempt.usage_cost == "0.01"
            (invocation,) = unit.assistant.list_tool_invocations(turn_ids[1])
            assert invocation.status.value == "running"
    finally:
        restored.close()


def test_cancelled_stream_query_is_local_tenant_scoped_and_bounded(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "scopes.db")
    expected = {}
    try:
        for tenant in ("a", "b"):
            factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant)
            expected[tenant] = set()
            for index in range(3):
                task, scope = _seed_task(factory, label=str(index))
                turn = repository_turn(task, scope, key=f"query:{index}")
                turn.cancel()
                attempt = ProviderAttempt.create(
                    turn=turn, model_round=1, attempt_number=1,
                    profile_id="scripted", model_id="scripted",
                    endpoint_kind=ModelEndpointKind.CHAT,
                    model_role=ModelExecutionRole.PRIMARY,
                )
                with factory() as unit:
                    unit.assistant.save_turn(turn)
                    unit.assistant.save_provider_attempt(attempt)
                    unit.commit()
                if index == 2:
                    with engine.begin() as connection:
                        connection.execute(update(tasks).where(
                            tasks.c.tenant_id == tenant, tasks.c.id == str(task.id),
                        ).values(execution_target="cloud"))
                else:
                    expected[tenant].add(attempt.id)
        for tenant in ("b", "a"):
            factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant)
            with factory() as unit:
                actual = {item.id for item in unit.assistant.cancelled_provider_attempts()}
                assert actual == expected[tenant]
                assert len(unit.assistant.cancelled_provider_attempts(limit=1)) == 1
    finally:
        engine.dispose()
