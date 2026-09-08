from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest

from fairy_core.assistant.routing import QWEN_FREE_MODEL_ID
from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
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


def _clarification_classification(profile_id: str) -> tuple[ModelDelta, ...]:
    return (
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
    )


def _resolved_clarification_classification(
    profile_id: str,
    target: str,
) -> tuple[ModelDelta, ...]:
    return (
        ModelDelta.text(
            profile_id=profile_id,
            sequence=1,
            text=json.dumps(
                {
                    "evidence_requirements": [],
                    "requires_workspace_changes": False,
                    "public_summary": "The durable change target is resolved.",
                    "interpretation": {
                        "normalized_goal": f"Update {target}.",
                        "action": "change",
                        "objectives": [
                            {
                                "goal": f"Update {target}.",
                                "action": "change",
                                "depends_on": [],
                            }
                        ],
                        "targets": [target],
                        "constraints": [],
                        "deliverable": "Updated project file",
                        "assumptions": [],
                        "missing_information": [],
                        "confidence": "high",
                        "disposition": "ready",
                        "public_summary": f"Update {target}.",
                        "clarification_question": None,
                    },
                }
            ),
        ),
        ModelDelta.done(profile_id=profile_id, sequence=2, finish_reason="stop"),
    )


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


@pytest.mark.parametrize("original", ["Draft a response", "Notify me when ready"])
def test_steering_revises_one_turn_and_replays_idempotently(tmp_path: Path, original: str) -> None:
    provider = _BoundaryProvider()
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        task = _scratch_task(service, original)
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
        interpretation = service.invoke(
            "assistant.turns.interpretation.get",
            {"turn_id": turn["id"]},
        )
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]
        replayed = service.invoke("assistant.turns.steer", request)

        assert completed["status"] == "completed"
        assert workflow["active_plan_revision"] == 2
        assert interpretation["revision"] == 2
        assert interpretation["action"] == "answer"
        assert interpretation["targets"] == []
        assert interpretation["constraints"][-1] == (
            "Updated requirement: Focus on the recovery behavior instead."
        )
        assert [message["content"] for message in messages] == [
            original,
            "Focus on the recovery behavior instead.",
            "Updated response",
        ]
        assert replayed["id"] == turn["id"]
        assert provider.call_count == 2
    finally:
        provider.release.set()
        service.close()


@pytest.mark.parametrize("blocked_call", [1, 2])
def test_steering_reclassifies_the_new_source_before_continuing(tmp_path: Path, blocked_call: int):
    profile_id = "openrouter-qwen-free"

    class ReclassifyingProvider(ScriptedProvider):
        def __init__(self):
            super().__init__(
                [],
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
            self.started = Event()
            self.release = Event()

        def stream(self, request, cancellation):
            self.requests.append(request)
            count = len(self.requests)
            if count == blocked_call:
                self.started.set()
                assert self.release.wait(timeout=5)
                cancellation.raise_if_cancelled()
            if count in {1, blocked_call + 1}:
                target = "src/a.ts" if count == 1 else "src/b.ts"
                yield from _resolved_clarification_classification(profile_id, target)
                return
            yield ModelDelta.text(profile_id=profile_id, sequence=1, text="Scoped response")
            yield ModelDelta.done(profile_id=profile_id, sequence=2, finish_reason="stop")

    provider = ReclassifyingProvider()
    service = build_local_service(tmp_path, provider_registry=ProviderRegistry((provider,)))
    try:
        selection = service.invoke(
            "models.selection.update",
            {
                "mode": "manual",
                "model_id": QWEN_FREE_MODEL_ID,
                "allow_free_fallback": False,
                "zero_data_retention": False,
                "expected_revision": 0,
                "idempotency_key": "select",
            },
        )
        selection = service.invoke("models.selection.get", {})
        task = _scratch_task(service, "Update src/a.ts")
        turn = service.invoke(
            "assistant.turns.create",
            {
                "task_id": task["id"],
                "idempotency_key": "steering-reclassify",
                "model_selection": {
                    key: selection[key] for key in ("mode", "model_id", "revision")
                },
            },
        )
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        assert provider.started.wait(timeout=5)
        service.invoke(
            "assistant.turns.steer",
            {
                "turn_id": turn["id"],
                "instruction": "Update src/b.ts instead",
                "expected_revision": 1,
                "idempotency_key": "steer:b",
            },
        )
        provider.release.set()
        wait_for_turn(service, turn["id"])
        interpretation = service.invoke(
            "assistant.turns.interpretation.get", {"turn_id": turn["id"]}
        )
        assert interpretation["targets"] == ["src/b.ts"]
        assert interpretation["revision"] == blocked_call + 1
        assert len(provider.requests) == blocked_call + 2
        assert "Update src/b.ts instead" in provider.requests[blocked_call].messages[-1].content
    finally:
        provider.release.set()
        service.close()


def test_clarification_waits_and_resumes_the_same_turn_idempotently(
    tmp_path: Path,
    record_property,
) -> None:
    profile_id = "openrouter-qwen-free"
    provider = ScriptedProvider(
        [
            _clarification_classification(profile_id),
            _clarification_classification(profile_id),
            _resolved_clarification_classification(profile_id, "src/app.ts"),
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
        wait_for_turn(service, turn["id"], status="waiting_for_input")
        _wait_for_workflow(service, turn["id"], "waiting_for_input")
        waiting = service.invoke("assistant.turns.get", {"turn_id": turn["id"]})
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            workflow = unit_of_work.workflows.get(UUID(waiting["workflow_run_id"]))
        assert workflow is not None
        execution_nodes_started = sum(
            node.attempt_count
            for node in workflow.nodes
            if node.kind in {"assistant.model.round", "assistant.tool.invoke"}
        )
        record_property(
            "execution_nodes_started_before_clarification",
            execution_nodes_started,
        )
        interpretation = service.invoke(
            "assistant.turns.interpretation.get",
            {"turn_id": turn["id"]},
        )
        waiting_events = service.invoke("events.list", {"cursor": 0, "limit": 100})["items"]
        background = service.invoke(
            "assistant.background_tasks.list",
            {"current_conversation_id": task["conversation_id"], "recent_limit": 20},
        )
        response = {
            "turn_id": turn["id"],
            "content": "Maybe the app file.",
            "expected_interpretation_revision": 1,
            "idempotency_key": "clarification:target",
        }

        with pytest.raises(VersionConflictError):
            service.invoke(
                "assistant.turns.respond",
                {
                    **response,
                    "expected_interpretation_revision": 2,
                    "idempotency_key": "clarification:stale",
                },
            )

        resumed = service.invoke("assistant.turns.respond", response)
        replayed = service.invoke("assistant.turns.respond", response)
        with pytest.raises(IdempotencyConflictError):
            service.invoke(
                "assistant.turns.respond",
                {**response, "content": "Update a different file."},
            )
        wait_for_turn(service, turn["id"], status="waiting_for_input")
        repeated = service.invoke(
            "assistant.turns.interpretation.get",
            {"turn_id": turn["id"]},
        )
        assert repeated["revision"] == 3
        assert repeated["disposition"] == "clarification_required"

        service.invoke(
            "assistant.turns.respond",
            {
                "turn_id": turn["id"],
                "content": "Update src/app.ts.",
                "expected_interpretation_revision": 3,
                "idempotency_key": "clarification:resolved-target",
            },
        )
        completed = wait_for_turn(service, turn["id"])
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]

        assert waiting["workflow_summary"]["status"] == "waiting_for_input"
        assert interpretation["clarification_question"] == "Which file should Fairy update?"
        assert interpretation["revision"] == 1
        assert background["current"][0]["status"] == "waiting_for_input"
        assert background["current"][0]["attention_code"] == "clarification_required"
        assert background["current"][0]["public_error"] == ("Which file should Fairy update?")
        assert any(
            event["event_type"] == "assistant.turn.clarification_requested"
            and event["payload"].get("turn_id") == turn["id"]
            for event in waiting_events
        )
        assert resumed["id"] == turn["id"] == replayed["id"]
        assert completed["active_interpretation_revision"] == 5
        assert completed["interpretation_summary"]["disposition"] == "ready"
        assert [(message["role"], message["content"]) for message in messages] == [
            ("user", "Update it"),
            ("user", "Maybe the app file."),
            ("user", "Update src/app.ts."),
            ("assistant", "Updated the clarified target."),
        ]
        assert len(provider.requests) == 4
        assert "PRIOR_INTERPRETATION" in provider.requests[1].messages[0].content
        clarification_payload = json.loads(provider.requests[1].messages[-1].content)
        assert (
            "".join(segment["text"] for segment in clarification_payload["segments"])
            == "Maybe the app file."
        )
        resolved_payload = json.loads(provider.requests[2].messages[-1].content)
        assert (
            "".join(segment["text"] for segment in resolved_payload["segments"])
            == "Update src/app.ts."
        )
    finally:
        service.close()


def test_clarification_wait_survives_core_restart(tmp_path: Path) -> None:
    profile_id = "openrouter-qwen-free"
    first_provider = ScriptedProvider(
        [_clarification_classification(profile_id)],
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
    first_service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((first_provider,)),
    )
    try:
        first_service.invoke(
            "models.selection.update",
            {
                "mode": "manual",
                "model_id": QWEN_FREE_MODEL_ID,
                "allow_free_fallback": False,
                "zero_data_retention": False,
                "expected_revision": 0,
                "idempotency_key": "selection:clarification-restart",
            },
        )
        task = _scratch_task(first_service, "Update it after restart")
        selection = first_service.invoke("models.selection.get", {})
        turn = first_service.invoke(
            "assistant.turns.create",
            {
                "task_id": task["id"],
                "model_selection": {
                    "mode": selection["mode"],
                    "model_id": selection["model_id"],
                    "revision": selection["revision"],
                },
                "idempotency_key": "turn:clarification-restart",
            },
        )
        first_service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        _wait_for_workflow(first_service, turn["id"], "waiting_for_input")
    finally:
        first_service.close()

    second_provider = ScriptedProvider(
        [
            _resolved_clarification_classification(profile_id, "src/app.ts"),
            (
                ModelDelta.text(
                    profile_id=profile_id,
                    sequence=1,
                    text="Completed after clarification and restart.",
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
    second_service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((second_provider,)),
    )
    try:
        restored = second_service.invoke("assistant.turns.get", {"turn_id": turn["id"]})
        assert restored["status"] == "waiting_for_input"
        assert restored["workflow_summary"]["status"] == "waiting_for_input"

        second_service.invoke(
            "assistant.turns.respond",
            {
                "turn_id": turn["id"],
                "content": "Use src/app.ts.",
                "expected_interpretation_revision": 1,
                "idempotency_key": "clarification:restart-target",
            },
        )
        completed = wait_for_turn(second_service, turn["id"])

        assert completed["status"] == "completed"
        assert completed["active_interpretation_revision"] == 3
        assert len(second_provider.requests) == 2
    finally:
        second_service.close()
