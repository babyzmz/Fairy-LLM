from uuid import UUID

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from fairy_core.execution.plans import ExecutionPlan
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.storage.sqlite_migrations import _sqlite_rebuild_transaction
from fairy_core.transports.stdio import build_local_service
from fairy_core.workflow.models import WorkflowNode, WorkflowPlanReason
from tests.assistant.test_application import _turn


def _task(service, key):
    conversation = service.invoke(
        "conversations.create",
        {
            "project_id": None,
            "workspace_type": "chat_scratch",
        },
    )
    return service.invoke(
        "tasks.create",
        {
            "conversation_id": conversation["id"],
            "user_request": "Build the requested files",
            "operation_mode": "continue_current_chat_draft",
            "execution_target": "local",
            "idempotency_key": key,
        },
    )["task"]


def _manifest(path):
    return {
        "files": [{"path": path, "purpose": "Requested file", "batch": 1}],
        "entrypoints": [],
        "dependencies": [],
        "validation_commands": [],
    }


def test_plan_generations_preserve_history_and_latest_task_lookup_after_reopen(tmp_path):
    service = build_local_service(tmp_path, assistant_workflow_engine_version=4)
    service._workflow_scheduler.close()
    try:
        task, other_task = _task(service, "first"), _task(service, "other")
        turn = _turn(service, task, "versioned-plan")
        run_id = UUID(turn["workflow_run_id"])
        with service._unit_of_work_factory() as unit:
            owned_task = unit.state.get_task(UUID(task["id"]))
            first, first_steps = ExecutionPlan.create(
                task=owned_task,
                manifest=_manifest("before.txt"),
                generation=1,
                workflow_run_id=run_id,
                workflow_plan_revision=1,
            )
            first.cancel()
            unit.state.save_execution_plan(first, first_steps)
            instruction = unit.workflows.add_instruction(
                run_id,
                instruction="Use after.txt instead",
                expected_revision=1,
                idempotency_key="new-file-plan",
            )
            unit.workflows.append_plan(
                run_id,
                expected_revision=1,
                reason=WorkflowPlanReason.STEERING,
                instruction_id=instruction.id,
                nodes=(
                    WorkflowNode.create(
                        run_id=run_id,
                        plan_revision=2,
                        node_key="revised",
                        kind="test.pending",
                        payload={},
                        public_summary="New authorized plan",
                    ),
                ),
                edges=(),
            )
            second, second_steps = ExecutionPlan.create(
                task=owned_task,
                manifest=_manifest("after.txt"),
                generation=2,
                workflow_run_id=run_id,
                workflow_plan_revision=2,
            )
            unit.state.save_execution_plan(second, second_steps)
            other, other_steps = ExecutionPlan.create(
                task=unit.state.get_task(UUID(other_task["id"])),
                manifest=_manifest("other.txt"),
            )
            unit.state.save_execution_plan(other, other_steps)
            unit.commit()
        # The task generation, Workflow revision and revision FK are separate constraints.
        for generation, revision in ((2, None), (3, 2), (3, 999)):
            with service._unit_of_work_factory() as unit:
                duplicate, steps = ExecutionPlan.create(
                    task=owned_task,
                    manifest=_manifest("duplicate.txt"),
                    generation=generation,
                    workflow_run_id=run_id if revision is not None else None,
                    workflow_plan_revision=revision,
                )
                with pytest.raises(IntegrityError):
                    unit.state.save_execution_plan(duplicate, steps)
    finally:
        service.close()
    for _ in range(2):
        engine = create_sqlite_core_engine(tmp_path / "core.db")
        try:
            with SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")() as unit:
                assert unit.state.execution_plan_for_task(first.task_id).id == second.id
                assert unit.state.get_execution_plan(first.id).manifest == _manifest("before.txt")
                assert unit.state.task_steps_for_plan(first.id) == list(first_steps)
                assert unit.state.execution_plan_for_task(other.task_id).id == other.id
                assert unit.state.get_execution_plan(second.id).workflow_plan_revision == 2
                assert not unit._connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            with SqlAlchemyUnitOfWorkFactory(engine, tenant_id="another-tenant")() as unit:
                assert unit.state.get_execution_plan(first.id) is None
                assert unit.state.execution_plan_for_task(second.task_id) is None
                foreign, foreign_steps = ExecutionPlan.create(
                    task=owned_task,
                    manifest=_manifest("foreign.txt"),
                    generation=3,
                    workflow_run_id=run_id,
                    workflow_plan_revision=2,
                )
                with pytest.raises(IntegrityError):
                    unit.state.save_execution_plan(foreign, foreign_steps)
        finally:
            engine.dispose()


def test_legacy_plan_upgrade_keeps_steps_and_foreign_keys_after_reopen(tmp_path):
    service = build_local_service(tmp_path)
    service._workflow_scheduler.close()
    try:
        task, other_task = _task(service, "legacy"), _task(service, "legacy-other")
        with service._unit_of_work_factory() as unit:
            plans = []
            for data in (task, other_task):
                plan, steps = ExecutionPlan.create(
                    task=unit.state.get_task(UUID(data["id"])),
                    manifest=_manifest(f"{data['id']}.txt"),
                )
                unit.state.save_execution_plan(plan, steps)
                plans.append((plan, steps))
            unit.commit()
    finally:
        service.close()
    # Reconstruct the real previous schema, including populated child rows.
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    with _sqlite_rebuild_transaction(engine) as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("core_execution_plans", recreate="always") as batch:
            batch.drop_constraint("uq_core_execution_plans_generation", type_="unique")
            batch.drop_constraint("uq_core_execution_plans_workflow_revision", type_="unique")
            batch.drop_constraint("fk_core_execution_plans_workflow_revision", type_="foreignkey")
            batch.drop_constraint("ck_core_execution_plans_generation", type_="check")
            batch.drop_constraint("ck_core_execution_plans_workflow_binding", type_="check")
            for name in ("generation", "workflow_run_id", "workflow_plan_revision"):
                batch.drop_column(name)
            batch.create_unique_constraint("uq_core_execution_plans_task", ["tenant_id", "task_id"])
    engine.dispose()
    for _ in range(2):
        engine = create_sqlite_core_engine(tmp_path / "core.db")
        try:
            with SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")() as unit:
                for plan, steps in plans:
                    restored = unit.state.execution_plan_for_task(plan.task_id)
                    assert restored == plan
                    assert restored.generation == 1
                    assert restored.workflow_run_id is None
                    assert unit.state.task_steps_for_plan(plan.id) == list(steps)
                assert not unit._connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                assert unit._connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            uniques = {
                item["name"]
                for item in inspect(engine).get_unique_constraints(
                    "core_execution_plans",
                )
            }
            assert "uq_core_execution_plans_generation" in uniques
            assert "uq_core_execution_plans_task" not in uniques
        finally:
            engine.dispose()
