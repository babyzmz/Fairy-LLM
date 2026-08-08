from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from threading import Event

from fairy_core.assistant.routing import QWEN_FREE_MODEL_ID
from fairy_core.providers import (
    CancellationToken,
    ModelDelta,
    ModelRequest,
    ProviderCapability,
    ProviderRegistry,
)
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


def test_clarification_waits_and_resumes_the_same_turn_idempotently(tmp_path: Path) -> None:
    profile_id = "openrouter-qwen-free"
    provider = ScriptedProvider(
        [
            (
                ModelDelta.text(
                    profile_id=profile_id,
                    sequence=1,
                    text=json.dumps(
                        {
                            "evidence_requirements": [],
                            "requires_workspace_changes": False,
                            "public_summary": "Clarify the durable change target.",
                            "interpretation": {
                                "normalized_goal": "Update the requested project file.",
                                "action": "change",
                                "objectives": [
                                    {
                                        "goal": "Update the requested project file.",
                                        "action": "change",
                                        "depends_on": [],
                                    }
                                ],
                                "targets": [],
                                "constraints": [],
                                "deliverable": "Updated project file",
                                "assumptions": [],
                                "missing_information": ["target file"],
                                "confidence": "low",
                                "disposition": "clarification_required",
                                "public_summary": "The target file is missing.",
                                "clarification_question": "Which file should Fairy update?",
                            },
                        }
                    ),
                ),
                ModelDelta.done(profile_id=profile_id, sequence=2, finish_reason="stop"),
            ),
            (
                ModelDelta.text(
                    profile_id=profile_id,
                    sequence=1,
                    text="Updated the clarified target.",
                ),
                ModelDelta.done(profile_id=profile_id, sequence=2, finish_reason="stop"),
            ),
        ],
        profile_id=profile_id,
        model_id=QWEN_FREE_MODEL_ID,
        capabilities=frozenset(
            {
                ProviderCapability.TEXT,
                ProviderCapability.TOOLS,
                ProviderCapability.STRUCTURED_OUTPUT,
            }
        ),
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        service.invoke(
            "models.selection.update",
            {
                "mode": "manual",
                "model_id": QWEN_FREE_MODEL_ID,
                "allow_free_fallback": False,
                "zero_data_retention": False,
                "expected_revision": 0,
                "idempotency_key": "selection:clarification",
            },
        )
        task = _scratch_task(service, "Update it")
        selection = service.invoke("models.selection.get", {})
        turn = service.invoke(
            "assistant.turns.create",
            {
                "task_id": task["id"],
                "model_selection": {
                    "mode": selection["mode"],
                    "model_id": selection["model_id"],
                    "revision": selection["revision"],
                },
                "idempotency_key": "turn:clarification",
            },
        )

        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        waiting = wait_for_turn(service, turn["id"], status="waiting_for_input")
        interpretation = service.invoke(
            "assistant.turns.interpretation.get",
            {"turn_id": turn["id"]},
        )
        response = {
            "turn_id": turn["id"],
            "content": "Update src/app.ts.",
            "expected_interpretation_revision": 1,
            "idempotency_key": "clarification:target",
        }

        resumed = service.invoke("assistant.turns.respond", response)
        replayed = service.invoke("assistant.turns.respond", response)
        completed = wait_for_turn(service, turn["id"])
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]

        assert waiting["workflow_summary"]["status"] == "waiting_for_input"
        assert interpretation["clarification_question"] == "Which file should Fairy update?"
        assert interpretation["revision"] == 1
        assert resumed["id"] == turn["id"] == replayed["id"]
        assert completed["active_interpretation_revision"] == 2
        assert completed["interpretation_summary"]["disposition"] == "ready"
        assert [(message["role"], message["content"]) for message in messages] == [
            ("user", "Update it"),
            ("user", "Update src/app.ts."),
            ("assistant", "Updated the clarified target."),
        ]
        assert len(provider.requests) == 2
    finally:
        service.close()
