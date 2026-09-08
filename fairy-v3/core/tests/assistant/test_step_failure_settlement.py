from uuid import UUID

from fairy_core.providers import ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.test_application import _scratch_task, _turn
from tests.assistant.test_step_workflow import _provider


def test_failed_verification_settles_workflow_turn_and_task_without_affecting_other_chat(
    tmp_path,
    monkeypatch,
):
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((_provider(),)),
        assistant_workflow_engine_version=4,
    )
    try:
        task = _scratch_task(service, "Hello Fairy")
        turn = _turn(service, task, "failed-verification")
        other_task = _scratch_task(service, "Keep the other conversation untouched")
        other = _turn(service, other_task, "unaffected-conversation")

        def broken(*args, **kwargs):
            raise RuntimeError("Internal diagnostic which must not become public content")

        monkeypatch.setattr(service._assistant_application, "_execution_completion_issue", broken)
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        failed = service._workflow_scheduler.wait(UUID(turn["workflow_run_id"]), timeout=5)
        assert failed.run.status == "failed"
        with service._unit_of_work_factory() as unit:
            persisted = unit.assistant.get_turn(UUID(turn["id"]))
            current_task = unit.state.get_task(UUID(task["id"]))
            untouched = unit.assistant.get_turn(UUID(other["id"]))
            other_run = unit.workflows.get(UUID(other["workflow_run_id"]))
            trace = unit.assistant.list_trace_steps(persisted.id)
        assert persisted.status == "failed"
        assert persisted.error_code == failed.run.error_code == "WORKFLOW_NODE_FAILED"
        assert current_task.status == "failed"
        assert untouched.status == other["status"]
        assert other_run.run.status == "paused"
        assert all(step.status not in {"pending", "running", "waiting"} for step in trace)
    finally:
        service.close()
