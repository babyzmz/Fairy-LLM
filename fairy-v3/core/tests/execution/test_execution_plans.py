from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.execution.plans import TaskStepStatus
from fairy_core.transports.stdio import build_local_service


def _scratch_task(service) -> dict[str, object]:
    conversation = service.invoke(
        "conversations.create",
        {"project_id": None, "workspace_type": "chat_scratch"},
    )
    return service.invoke(
        "tasks.create",
        {
            "conversation_id": conversation["id"],
            "user_request": "Build a multi-file application",
            "operation_mode": "continue_current_chat_draft",
            "execution_target": "local",
            "idempotency_key": "plan:task",
        },
    )


def _plan_request(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "files": [
            {"path": "package.json", "purpose": "Node scripts", "batch": 1},
            {"path": "src/main.ts", "purpose": "Frontend entry", "batch": 1},
            {"path": "server/index.ts", "purpose": "API entry", "batch": 2},
        ],
        "entrypoints": ["src/main.ts", "server/index.ts"],
        "dependencies": ["react", "express"],
        "validation_commands": ["npm test", "npm run build"],
    }


def test_scratch_execution_plan_is_durable_and_idempotent(tmp_path: Path) -> None:
    service = build_local_service(tmp_path / "data")
    try:
        task = _scratch_task(service)
        request = _plan_request(task["task"]["id"])

        created = service.invoke("execution_plans.create", request)
        replayed = service.invoke("execution_plans.create", request)
        fetched = service.invoke(
            "execution_plans.get",
            {"task_id": task["task"]["id"]},
        )

        assert replayed == created
        assert fetched == created
        assert created["plan"]["workspace_id"] == task["task"]["workspace_id"]
        assert created["plan"]["version_id"] == task["task"]["target_version_id"]
        assert [step["kind"] for step in created["steps"]] == [
            "analyze",
            "file_plan",
            "implement",
            "implement",
            "install",
            "test",
            "preview",
            "repair",
            "summary",
            "checkpoint",
        ]
        assert [step["status"] for step in created["steps"][:2]] == [
            "completed",
            "completed",
        ]
    finally:
        service.close()


def test_execution_plan_rejects_a_different_manifest_for_the_same_task(
    tmp_path: Path,
) -> None:
    service = build_local_service(tmp_path / "data")
    try:
        task = _scratch_task(service)
        request = _plan_request(task["task"]["id"])
        service.invoke("execution_plans.create", request)
        request["files"] = [{"path": "other.txt", "purpose": "Different plan", "batch": 1}]

        with pytest.raises(IdempotencyConflictError):
            service.invoke("execution_plans.create", request)
    finally:
        service.close()


def test_task_step_and_plan_budgets_reject_invalid_transitions(tmp_path: Path) -> None:
    service = build_local_service(tmp_path / "data")
    try:
        task = _scratch_task(service)
        created = service.invoke(
            "execution_plans.create",
            _plan_request(task["task"]["id"]),
        )
        with service._unit_of_work_factory() as unit_of_work:
            plan = unit_of_work.state.get_execution_plan(created["plan"]["id"])
            assert plan is not None
            step = unit_of_work.state.task_steps_for_plan(plan.id)[2]

        with pytest.raises(InvalidTransitionError):
            step.transition_to(TaskStepStatus.COMPLETED)
        for _ in range(plan.max_repairs):
            plan.consume_repair()
        with pytest.raises(InvalidTransitionError, match="repair budget exhausted"):
            plan.consume_repair()
        assert plan.status.value == "paused"
    finally:
        service.close()


def test_file_batches_are_complete_and_run_in_order(tmp_path: Path) -> None:
    service = build_local_service(tmp_path / "data")
    try:
        task = _scratch_task(service)
        task_id = UUID(task["task"]["id"])
        service.invoke("execution_plans.create", _plan_request(str(task_id)))
        planning = service._application.execution_planning

        with pytest.raises(ValueError, match="complete planned file batch"):
            planning.start_file_batch(task_id, ("package.json",))
        with pytest.raises(ValueError, match="must run in order"):
            planning.start_file_batch(task_id, ("server/index.ts",))

        first = ("package.json", "src/main.ts")
        assert planning.start_file_batch(task_id, first).status.value == "running"
        assert planning.complete_file_batch(task_id, first).status.value == "completed"
        assert (
            planning.start_file_batch(
                task_id,
                ("server/index.ts",),
            ).status.value
            == "running"
        )
    finally:
        service.close()


def test_changeset_rejects_more_than_two_mib_across_a_batch(tmp_path: Path) -> None:
    service = build_local_service(tmp_path / "data")
    try:
        task = _scratch_task(service)
        with pytest.raises(ValueError, match="2 MiB"):
            service.invoke(
                "changesets.propose",
                {
                    "task_id": task["task"]["id"],
                    "files": [
                        {"path": "a.txt", "content": "a" * (1024 * 1024 + 1)},
                        {"path": "b.txt", "content": "b" * (1024 * 1024)},
                    ],
                    "reason": "Oversized atomic batch",
                    "idempotency_key": "changeset:oversized",
                },
            )
    finally:
        service.close()
