from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import event

from fairy_core.assistant.background_tasks import AssistantBackgroundTaskProjection
from fairy_core.assistant.models import AssistantTurnStatus, ProviderAttempt
from fairy_core.assistant.schedule_models import AssistantScheduleOccurrence
from fairy_core.contracts.assistant_schedules import AssistantBackgroundTaskListInput
from fairy_core.contracts.common import ExecutionTarget
from fairy_core.model_catalog.models import ModelEndpointKind
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.providers import ModelExecutionRole
from fairy_core.workflow.models import WorkflowNode, WorkflowRun, WorkflowTriggerKind
from tests.assistant.test_repository_contract import _intent_fixture, _seed_task, _turn
from tests.assistant.test_schedule_repository import _schedule


def test_background_projection_batches_twenty_workflows_and_schedule_bindings(tmp_path):
    path = tmp_path / "background.db"
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    conversations = []
    for index in range(20):
        task, scope = _seed_task(factory, label=f"background-{index}")
        conversations.append(task.conversation_id)
        turn = _turn(task, scope, key=f"turn-{index}")
        run = WorkflowRun.create(
            owner_kind="assistant_turn",
            owner_id=str(turn.id),
            execution_target=ExecutionTarget.LOCAL,
            trigger_kind=WorkflowTriggerKind.USER_TURN,
            idempotency_key=f"workflow-{index}",
            conversation_id=task.conversation_id,
            task_id=task.id,
            engine_version=3,
        )
        node = WorkflowNode.create(
            run_id=run.id,
            plan_revision=1,
            node_key="model",
            kind="assistant.model",
            payload={"private_payload": "not a projection field"},
            public_summary="Preparing",
        )
        turn.workflow_run_id, turn.execution_engine_version = run.id, 3
        schedule = _schedule(task, key=f"schedule-{index}")
        occurrence = AssistantScheduleOccurrence.pending(
            schedule_id=schedule.id,
            schedule_revision=1,
            scheduled_for=datetime.now(UTC),
        ).dispatch(turn_id=turn.id, workflow_run_id=run.id, now=datetime.now(UTC))
        with factory() as unit:
            unit.workflows.create(run, nodes=(node,), edges=())
            unit.assistant.save_turn(turn)
            unit.assistant_schedules.create(schedule)
            unit.assistant_schedules.create_occurrence(occurrence)
            unit.commit()
    engine.dispose()
    reopened = create_sqlite_core_engine(path)
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    try:
        event.listen(reopened, "after_cursor_execute", capture)
        projection = AssistantBackgroundTaskProjection(
            SqlAlchemyUnitOfWorkFactory(reopened, tenant_id="local"),
        )
        result = projection.list(
            AssistantBackgroundTaskListInput(
                current_conversation_id=conversations[0],
            )
        )
        assert len(result["current"]) == 1
        assert len(result["other"]) == 19
        assert result["nonterminal_count"] == 20
        assert all(
            item["kind"] == "scheduled_turn" for item in (*result["current"], *result["other"])
        )
        assert len(statements) <= 12, f"20 displayed tasks required {len(statements)} SELECTs"
        print(f"background_20_tasks_selects={len(statements)}")
        assert all("core_workflow_nodes.payload" not in statement for statement in statements)
        other = AssistantBackgroundTaskProjection(
            SqlAlchemyUnitOfWorkFactory(reopened, tenant_id="other"),
        ).list(AssistantBackgroundTaskListInput(current_conversation_id=conversations[0]))
        assert other["nonterminal_count"] == 0
        assert other["recent"] == ()
    finally:
        reopened.dispose()


def test_batch_turn_projection_preserves_interpretation_and_pending_cancellation(tmp_path):
    engine = create_sqlite_core_engine(tmp_path / "projection-parity.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    fixtures = [_intent_fixture(factory, label=label) for label in ("left", "right")]
    try:
        with factory() as unit:
            for _, _, _, interpretation in fixtures:
                unit.assistant.append_interpretation(interpretation, expected_revision=None)
            unit.commit()
        with factory() as unit:
            left = unit.assistant.get_turn(fixtures[0][1].id)
            attempt = ProviderAttempt.create(
                turn=left,
                model_round=1,
                attempt_number=1,
                profile_id="test",
                model_id="test",
                endpoint_kind=ModelEndpointKind.CHAT,
                model_role=ModelExecutionRole.PRIMARY,
            )
            unit.assistant.save_provider_attempt(attempt)
            left.cancel()
            unit.assistant.update_turn(
                left,
                expected_status=AssistantTurnStatus.CREATED,
                expected_cancellation_revision=0,
            )
            unit.commit()
        with factory() as unit:
            batch = {turn.id: turn for turn in unit.assistant.list_turns()}
            for _, source, _, interpretation in fixtures:
                single = unit.assistant.get_turn(source.id)
                assert batch[source.id] == single
                assert batch[source.id].interpretation_summary.id == interpretation.id
            assert batch[fixtures[0][1].id].cancellation_pending
            assert not batch[fixtures[1][1].id].cancellation_pending
    finally:
        engine.dispose()
