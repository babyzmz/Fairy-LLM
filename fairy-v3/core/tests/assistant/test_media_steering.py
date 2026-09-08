import json
from functools import partial
from threading import Event
from time import monotonic, sleep
from uuid import UUID

import pytest

from fairy_core.assistant.application import AssistantApplication
from fairy_core.workflow.repository import SqlAlchemyWorkflowRepository
from fairy_core.workflow.scheduler import WorkflowPaused, WorkflowRetryableError
from tests.assistant.support import wait_for_turn
from tests.assistant.test_step_media import _create_media_turn
from tests.media import test_media_service as contracts


@pytest.mark.parametrize("lost_handoff", [False, True])
@pytest.mark.parametrize("control", ["steer", "pause"])
def test_steering_waits_for_deferred_media_outcome(tmp_path, monkeypatch, lost_handoff, control):
    monkeypatch.setattr(contracts, "build_local_service", partial(
        contracts.build_local_service, assistant_workflow_engine_version=4,
    ))
    entered, release = Event(), Event()
    original = contracts.RecordingMediaProvider.generate_image
    original_defer = SqlAlchemyWorkflowRepository.defer
    lost = []

    def defer(repository, claim, **kwargs):
        node = repository.get_node(claim.run_id, claim.node_id)
        if lost_handoff and not lost and node.kind == "assistant.step.tool":
            lost.append(claim.node_id)
            raise WorkflowRetryableError("Injected lost handoff acknowledgement")
        return original_defer(repository, claim, **kwargs)

    monkeypatch.setattr(SqlAlchemyWorkflowRepository, "defer", defer)

    def blocked(self, request, cancellation):
        entered.set()
        assert release.wait(10)
        return original(self, request, cancellation)

    monkeypatch.setattr(contracts.RecordingMediaProvider, "generate_image", blocked)
    service, provider, media, turn, task = _create_media_turn(tmp_path)
    try:
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        assert entered.wait(4)
        run_id = UUID(turn["workflow_run_id"])
        deadline = monotonic() + 2
        while monotonic() < deadline:
            with service._unit_of_work_factory() as unit:
                snapshot = unit.workflows.get(run_id)
            tool = next(node for node in snapshot.nodes if node.kind == "assistant.step.tool")
            if tool.status == "ready":
                break
            sleep(0.02)
        assert tool.status == "ready"
        instruction = "Describe the existing result. Do not generate another image."
        profile = provider.profile.id
        interpretation = {
            "normalized_goal": instruction, "action": "answer",
            "objectives": [{"goal": instruction, "action": "answer", "depends_on": []}],
            "targets": [], "constraints": ["Do not generate another image"],
            "deliverable": "Text description", "assumptions": [], "missing_information": [],
            "confidence": "high", "disposition": "ready", "public_summary": instruction,
            "clarification_question": None,
        }
        classification = (
            contracts.ModelDelta.text(profile_id=profile, sequence=1, text=json.dumps({
                "evidence_requirements": [], "requires_workspace_changes": False,
                "public_summary": instruction, "interpretation": interpretation,
            })),
            contracts.ModelDelta.done(profile_id=profile, sequence=2, finish_reason="stop"),
        )
        if control == "steer":
            provider.rounds.insert(0, classification)
            service.invoke("assistant.turns.steer", {
                "turn_id": turn["id"], "instruction": instruction,
                "expected_revision": 1, "idempotency_key": "steer:existing-media",
            })
        else:
            service.invoke("assistant.turns.pause", {"turn_id": turn["id"]})
        with service._unit_of_work_factory() as unit:
            paused = unit.workflows.get(run_id)
        assert paused.run.active_plan_revision == 1
        assert paused.run.pause_requested
        assert len(provider.requests) == 2
        assert next(node for node in paused.nodes if node.id == tool.id).status != "superseded"
        release.set()
        if control == "pause":
            deadline = monotonic() + 5
            while monotonic() < deadline:
                with service._unit_of_work_factory() as unit:
                    settled = unit.workflows.get(run_id)
                if next(node for node in settled.nodes if node.id == tool.id).status == "succeeded":
                    break
                sleep(0.02)
            assert next(node for node in settled.nodes if node.id == tool.id).status == "succeeded"
            assert settled.run.status == "paused"
            assert len(provider.requests) == 2
            service.invoke("assistant.turns.resume", {"turn_id": turn["id"]})
        completed = wait_for_turn(service, turn["id"], timeout_seconds=8)
        assert completed["status"] == "completed", completed
        with service._unit_of_work_factory() as unit:
            final = unit.workflows.get(run_id)
            invocations = unit.assistant.list_tool_invocations(UUID(turn["id"]))
            jobs = unit.state.list_media_jobs(UUID(task["id"]))
        assert final.run.active_plan_revision == (2 if control == "steer" else 1)
        assert len(jobs) == len(invocations) == len(media.image_requests) == 1
        assert invocations[0].status == "completed"
        assert next(node for node in final.nodes if node.id == tool.id).status == "succeeded"
        context = "\n".join(message.content for message in provider.requests[-1].messages)
        if control == "steer":
            assert instruction in context
        assert str(jobs[0].artifact_id) in context
        assert len(provider.requests) == (4 if control == "steer" else 3)
        assert bool(lost) == lost_handoff
    finally:
        release.set()
        service.close()


def test_pause_before_media_dispatch_does_not_spin_or_execute(tmp_path, monkeypatch):
    monkeypatch.setattr(contracts, "build_local_service", partial(
        contracts.build_local_service, assistant_workflow_engine_version=4,
    ))
    service, provider, media, turn, _task = _create_media_turn(tmp_path)
    original = AssistantApplication._execute_candidate
    stopped = Event()

    def pause_before_dispatch(app, **kwargs):
        if not stopped.is_set():
            service.invoke("assistant.turns.pause", {"turn_id": turn["id"]})
            stopped.set()
            raise WorkflowPaused
        return original(app, **kwargs)

    monkeypatch.setattr(AssistantApplication, "_execute_candidate", pause_before_dispatch)
    try:
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        assert stopped.wait(4)
        deadline = monotonic() + 1
        while monotonic() < deadline:
            with service._unit_of_work_factory() as unit:
                snapshot = unit.workflows.get(UUID(turn["workflow_run_id"]))
            node = next(node for node in snapshot.nodes if node.kind == "assistant.step.tool")
            assert node.attempt_count <= 2, "Paused unstarted work must not be repeatedly claimed"
            assert not media.image_requests
            sleep(0.02)
        assert snapshot.run.status == "paused"
        service.invoke("assistant.turns.resume", {"turn_id": turn["id"]})
        assert wait_for_turn(service, turn["id"], timeout_seconds=6)["status"] == "completed"
        assert len(media.image_requests) == 1
        assert len(provider.requests) == 3
    finally:
        service.close()
