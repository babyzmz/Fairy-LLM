from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from threading import Event

from fairy_core.providers import CancellationToken, ModelDelta, ModelRequest, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider, wait_for_turn
from tests.assistant.test_application import _scratch_task, _turn


class _BoundaryProvider(ScriptedProvider):
    def __init__(self) -> None:
        super().__init__([])
        self.started = Event()
        self.release = Event()
        self.call_count = 0

    def stream(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> Iterator[ModelDelta]:
        self.requests.append(request)
        self.call_count += 1
        if self.call_count == 1:
            self.started.set()
            assert self.release.wait(timeout=3.0)
            cancellation.raise_if_cancelled()
            yield ModelDelta.text(profile_id="scripted", sequence=1, text="Old response")
        else:
            yield ModelDelta.text(profile_id="scripted", sequence=1, text="Updated response")
        yield ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="stop")


def _wait_for_workflow(service, turn_id: str, status: str) -> dict[str, object]:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        workflow = service.invoke("assistant.turns.workflow.get", {"turn_id": turn_id})
        if workflow["status"] == status:
            return workflow
        time.sleep(0.01)
    raise AssertionError(f"Workflow did not reach {status}")


def test_active_turn_can_pause_and_resume_at_a_model_boundary(tmp_path: Path) -> None:
    provider = _BoundaryProvider()
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        task = _scratch_task(service, "Draft a response")
        turn = _turn(service, task, "turn:pause-resume")
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        assert provider.started.wait(timeout=3.0)

        pausing = service.invoke("assistant.turns.pause", {"turn_id": turn["id"]})

        assert pausing["workflow_summary"]["pause_requested"] is True
        provider.release.set()
        paused = _wait_for_workflow(service, turn["id"], "paused")
        assert paused["active_plan_revision"] == 1

        service.invoke("assistant.turns.resume", {"turn_id": turn["id"]})
        completed = wait_for_turn(service, turn["id"])

        assert completed["status"] == "completed"
        assert provider.call_count == 2
    finally:
        provider.release.set()
        service.close()


def test_steering_revises_one_turn_and_replays_idempotently(tmp_path: Path) -> None:
    provider = _BoundaryProvider()
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        task = _scratch_task(service, "Draft a response")
        turn = _turn(service, task, "turn:steer")
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        assert provider.started.wait(timeout=3.0)
        request = {
            "turn_id": turn["id"],
            "instruction": "Focus on the recovery behavior instead.",
            "expected_revision": 1,
            "idempotency_key": "steer:focus-recovery",
        }

        steering = service.invoke("assistant.turns.steer", request)

        assert steering["workflow_summary"]["pause_requested"] is True
        provider.release.set()
        completed = wait_for_turn(service, turn["id"])
        workflow = service.invoke("assistant.turns.workflow.get", {"turn_id": turn["id"]})
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]
        replayed = service.invoke("assistant.turns.steer", request)

        assert completed["status"] == "completed"
        assert workflow["active_plan_revision"] == 2
        assert [message["content"] for message in messages] == [
            "Draft a response",
            "Focus on the recovery behavior instead.",
            "Updated response",
        ]
        assert replayed["id"] == turn["id"]
        assert provider.call_count == 2
    finally:
        provider.release.set()
        service.close()
