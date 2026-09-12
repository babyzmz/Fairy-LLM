from types import SimpleNamespace
from uuid import UUID

import pytest

from fairy_core.assistant.interpretation import RequestAction
from fairy_core.assistant.routing import RoutingComplexity
from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.execution.plan_revisions import supersede_file_plan
from fairy_core.execution.plans import TaskStepKind
from fairy_core.transports.stdio import build_local_service
from fairy_core.workflow.models import WorkflowNode, WorkflowPlanReason
from tests.assistant.intent_support import bind_test_intent
from tests.assistant.test_application import _scratch_task, _turn
from tests.execution.test_plan_generations import _task


def test_revised_domain_plan_keeps_completed_facts_and_cannot_start_obsolete_work(tmp_path):
    service = build_local_service(tmp_path, assistant_workflow_engine_version=4)
    service._workflow_scheduler.close()
    try:
        task = _task(service, "file-plan-binding")
        turn = _turn(service, task, "versioned-domain-plan")
        task_id, run_id = UUID(task["id"]), UUID(turn["workflow_run_id"])
        planning = service._application.execution_planning
        request = {
            "task_id": task["id"],
            "files": [
                {"path": "before.txt", "purpose": "Original file", "batch": 1},
            ],
        }
        first = service.invoke("execution_plans.create", request)
        assert service.invoke("execution_plans.create", request) == first
        conflicting = {
            **request,
            "files": [{"path": "after.txt", "purpose": "New file", "batch": 1}],
        }
        with pytest.raises(IdempotencyConflictError):
            service.invoke("execution_plans.create", conflicting)
        planning.start_file_batch(task_id, ("before.txt",))
        with (
            service._unit_of_work_factory() as unit,
            pytest.raises(InvalidTransitionError, match="known outcome"),
        ):
            supersede_file_plan(unit, unit.assistant.get_turn(UUID(turn["id"])), 1)
        planning.complete_file_batch(task_id, ("before.txt",))
        with service._unit_of_work_factory() as unit:
            supersede_file_plan(unit, unit.assistant.get_turn(UUID(turn["id"])), 1)
            instruction = unit.workflows.add_instruction(
                run_id,
                instruction="Use after.txt",
                expected_revision=1,
                idempotency_key="revision-2",
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
                        public_summary="New plan",
                    ),
                ),
                edges=(),
            )
            unit.commit()
        budget = service._assistant_application._configure_workflow_budget(
            UUID(turn["id"]),
            SimpleNamespace(complexity=RoutingComplexity.HIGH),
        )
        assert budget.max_model_rounds == 24
        with pytest.raises(InvalidTransitionError):
            planning.start_file_batch(task_id, ("before.txt",))
        with pytest.raises(InvalidTransitionError):
            planning.start_step(task_id, TaskStepKind.PREVIEW)
        second = service.invoke("execution_plans.create", conflicting)
        assert second["plan"]["id"] != first["plan"]["id"]
        assert service.invoke("execution_plans.create", conflicting) == second
        with service._unit_of_work_factory() as unit:
            old = unit.state.get_execution_plan(UUID(first["plan"]["id"]))
            new = unit.state.get_execution_plan(UUID(second["plan"]["id"]))
            assert old.workflow_run_id == new.workflow_run_id == run_id
            assert (old.generation, old.workflow_plan_revision) == (1, 1)
            assert (new.generation, new.workflow_plan_revision) == (2, 2)
            assert old.status == "cancelled"
            assert old.max_model_calls == 12
            assert new.status == "active"
            assert (new.max_model_calls, new.max_tool_calls, new.max_duration_seconds) == (
                24,
                96,
                7200,
            )
            steps = unit.state.task_steps_for_plan(old.id)
            assert (
                next(step for step in steps if step.kind is TaskStepKind.IMPLEMENT).status
                == "completed"
            )
            assert all(step.status in {"completed", "skipped"} for step in steps)
    finally:
        service.close()


@pytest.mark.parametrize("engine_version", [3, 4])
@pytest.mark.parametrize(
    "action", [None, RequestAction.EXPLAIN, RequestAction.REVIEW, RequestAction.CHANGE]
)
def test_finalization_does_not_execute_a_readonly_or_prohibited_plan(
    tmp_path,
    monkeypatch,
    engine_version,
    action,
):
    service = build_local_service(tmp_path, assistant_workflow_engine_version=engine_version)
    service._workflow_scheduler.close()
    try:
        task = _scratch_task(service, "Discuss README.md. Do not execute anything.")
        turn = _turn(service, task, "readonly-file-plan")
        if action is not None:
            bind_test_intent(service, turn, action=action, targets=("README.md",))
        service.invoke(
            "execution_plans.create",
            {
                "task_id": task["id"],
                "files": [{"path": "README.md", "purpose": "Existing file", "batch": 1}],
                "entrypoints": ["README.md"],
                "validation_commands": ["npm test"],
            },
        )
        planning = service._application.execution_planning
        planning.start_file_batch(UUID(task["id"]), ("README.md",))
        planning.complete_file_batch(UUID(task["id"]), ("README.md",))

        def forbidden(*args, **kwargs):
            pytest.fail("Read-only finalization must not enter runtime automation")

        monkeypatch.setattr("fairy_core.application.service.select_runtime_template", forbidden)
        assert service._finalize_assistant_execution(UUID(turn["id"])) is None
        if action in {None, RequestAction.EXPLAIN, RequestAction.REVIEW}:
            assert (
                service._assistant_application._execution_completion_issue(
                    UUID(turn["id"]),
                    candidate_content="An explanation without edits.",
                )
                is None
            )
    finally:
        service.close()
