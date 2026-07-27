from __future__ import annotations

import time
from pathlib import Path
from uuid import UUID

import pytest

from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.realtime.tools import RealtimeAssistanceCapabilityError
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider

_FREE_MODEL_ID = "nvidia/nemotron-3-ultra-550b-a55b:free"
_PAID_MODEL_ID = "deepseek/deepseek-v4-pro"


def _service(
    tmp_path: Path,
    answer: str = "Use the [east gate](https://guide.test/east).",
    *,
    model_id: str = _FREE_MODEL_ID,
):
    provider = ScriptedProvider(
        [
            (
                ModelDelta.text(
                    profile_id="scripted",
                    sequence=1,
                    text=answer,
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            )
        ],
        model_id=model_id,
    )
    return build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )


def _session(service, *, model_id: str = _FREE_MODEL_ID):
    session = service.invoke(
        "realtime.sessions.start",
        {
            "device_id": "desktop-assistance",
            "provider": "auto",
            "locale": "en-AU",
            "microphone_consent": True,
            "screen_consent": True,
            "idempotency_key": "assistance-session",
        },
    )
    service.invoke(
        "models.selection.update",
        {
            "mode": "manual",
            "model_id": model_id,
            "allow_free_fallback": False,
            "zero_data_retention": False,
            "expected_revision": 0,
            "idempotency_key": "assistance-model-selection",
        },
    )
    return session


def _request(session, **changes):
    payload = {
        "session_id": session["id"],
        "conversation_id": session["conversation_id"],
        "request_id": "guide-1",
        "segment_id": "segment-1",
        "context_epoch": 1,
        "question": "Where is the hidden boss?",
        "activity_profile": "game",
        "application_title": "Test Game",
        "observed_facts": ["The map is open"],
        "allow_network": True,
        "locale": "en-AU",
    }
    payload.update(changes)
    return payload


def _wait_for_assistance(service, session, request_id="guide-1", timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = service.invoke(
            "realtime.assistance.get",
            {"session_id": session["id"], "request_id": request_id},
        )
        if current["status"] in {"completed", "failed", "cancelled"}:
            return current
        time.sleep(0.01)
    raise AssertionError("Realtime Assistance did not become terminal")


def _wait_for_assistance_status(service, session, status: str, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = service.invoke(
            "realtime.assistance.get",
            {"session_id": session["id"], "request_id": "guide-1"},
        )
        if current["status"] == status:
            return current
        if current["status"] in {"completed", "failed", "cancelled"}:
            raise AssertionError(
                f"Realtime Assistance became {current['status']!r}, expected {status!r}"
            )
        time.sleep(0.01)
    raise AssertionError(f"Realtime Assistance did not reach {status!r}")


def test_assistance_writes_full_answer_to_main_chat_and_returns_bounded_summary(
    tmp_path: Path,
) -> None:
    service = _service(
        tmp_path,
        "# Route\n\nUse the [east gate](https://guide.test/east) after sunset.",
    )
    try:
        session = _session(service)
        started = service.invoke("realtime.assistance.request", _request(session))
        completed = _wait_for_assistance(service, session)
        turn = service.invoke(
            "assistant.turns.get",
            {"turn_id": started["turn_id"]},
        )
        messages = service.invoke(
            "messages.list",
            {"conversation_id": session["conversation_id"], "limit": 100},
        )["items"]

        assert started["task_id"] is not None
        assert started["turn_id"] is not None
        assert completed["status"] == "completed"
        assert completed["spoken_summary"] == "Route"
        assert completed["display_markdown"].startswith("# Route")
        assert completed["citations"] == [
            {
                "title": "east gate",
                "url": "https://guide.test/east",
            }
        ]
        assert turn["knowledge_snapshot_id"] is not None
        assert turn["harness_manifest_id"] is not None
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert messages[-1]["id"] == completed["message_id"]
        assert messages[-1]["content"] == completed["display_markdown"]
        assert "Realtime context (public and user-visible)" in messages[0]["content"]
    finally:
        service.close()


def test_assistance_request_is_idempotent_and_conflicting_reuse_rejects(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    try:
        session = _session(service)
        first = service.invoke("realtime.assistance.request", _request(session))
        replay = service.invoke("realtime.assistance.request", _request(session))
        assert replay["id"] == first["id"]
        assert replay["task_id"] == first["task_id"]

        with pytest.raises(IdempotencyConflictError):
            service.invoke(
                "realtime.assistance.request",
                _request(session, question="Where is the treasure?"),
            )
    finally:
        service.close()


def test_paid_model_approval_stays_in_main_workspace_and_resumes_same_request(
    tmp_path: Path,
) -> None:
    service = _service(
        tmp_path,
        "The approved answer is ready.",
        model_id=_PAID_MODEL_ID,
    )
    try:
        session = _session(service, model_id=_PAID_MODEL_ID)
        started = service.invoke("realtime.assistance.request", _request(session))
        waiting = _wait_for_assistance_status(service, session, "awaiting_approval")
        approval = service.invoke(
            "approvals.list",
            {"task_id": started["task_id"]},
        )["items"][0]

        assert waiting["id"] == started["id"]
        assert waiting["requires_user_confirmation"] is True
        assert waiting["display_markdown"] is None
        decision = service.invoke(
            "approvals.decide",
            {"approval_id": approval["id"], "approved": True},
        )
        completed = _wait_for_assistance(service, session)

        assert decision["assistant_turn_id"] == started["turn_id"]
        assert completed["id"] == started["id"]
        assert completed["status"] == "completed"
        assert completed["display_markdown"] == "The approved answer is ready."
    finally:
        service.close()


def test_assistance_reconciles_same_turn_after_core_restart(tmp_path: Path) -> None:
    first = _service(
        tmp_path,
        "First provider must not run before approval.",
        model_id=_PAID_MODEL_ID,
    )
    session = _session(first, model_id=_PAID_MODEL_ID)
    try:
        started = first.invoke("realtime.assistance.request", _request(session))
        waiting = _wait_for_assistance_status(first, session, "awaiting_approval")
        assert waiting["turn_id"] == started["turn_id"]
    finally:
        first.close()

    reopened = _service(
        tmp_path,
        "Recovered Assistance answer.",
        model_id=_PAID_MODEL_ID,
    )
    try:
        restored = reopened.invoke(
            "realtime.assistance.get",
            {"session_id": session["id"], "request_id": "guide-1"},
        )
        approval = reopened.invoke(
            "approvals.list",
            {"task_id": restored["task_id"]},
        )["items"][0]
        reopened.invoke(
            "approvals.decide",
            {"approval_id": approval["id"], "approved": True},
        )
        completed = _wait_for_assistance(reopened, session)

        assert restored["id"] == started["id"]
        assert restored["status"] == "awaiting_approval"
        assert completed["turn_id"] == started["turn_id"]
        assert completed["display_markdown"] == "Recovered Assistance answer."
    finally:
        reopened.close()


def test_busy_conversation_keeps_assistance_queued_without_replacing_task(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    try:
        session = _session(service)
        active = service.invoke(
            "tasks.create",
            {
                "conversation_id": session["conversation_id"],
                "user_request": "Keep this task active",
                "operation_mode": "answer",
                "execution_target": "local",
                "idempotency_key": "active-task",
            },
        )
        queued = service.invoke("realtime.assistance.request", _request(session))
        conversation = service.invoke(
            "conversations.get",
            {"conversation_id": session["conversation_id"]},
        )

        assert queued["status"] == "queued"
        assert queued["error_code"] == "ASSISTANCE_CONVERSATION_BUSY"
        assert queued["task_id"] is None
        assert conversation["active_task_id"] == active["task"]["id"]
    finally:
        service.close()


def test_queued_assistance_cancel_is_revision_fenced(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        session = _session(service)
        service.invoke(
            "tasks.create",
            {
                "conversation_id": session["conversation_id"],
                "user_request": "Keep this task active",
                "operation_mode": "answer",
                "execution_target": "local",
                "idempotency_key": "active-task",
            },
        )
        queued = service.invoke("realtime.assistance.request", _request(session))
        with pytest.raises(VersionConflictError):
            service.invoke(
                "realtime.assistance.cancel",
                {
                    "session_id": session["id"],
                    "request_id": "guide-1",
                    "expected_revision": queued["revision"] - 1,
                },
            )
        cancelled = service.invoke(
            "realtime.assistance.cancel",
            {
                "session_id": session["id"],
                "request_id": "guide-1",
                "expected_revision": queued["revision"],
            },
        )
        assert cancelled["status"] == "cancelled"
    finally:
        service.close()


def test_assistance_tool_gate_denies_network_input_and_system_actions(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    try:
        session = _session(service)
        started = service.invoke(
            "realtime.assistance.request",
            _request(session, allow_network=False),
        )
        assert started["task_id"] is not None
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            task = unit_of_work.state.get_task(UUID(started["task_id"]))
            assert task is not None
            scope = service._application.scope_for_task(  # type: ignore[attr-defined]
                unit_of_work.state,
                task,
            )
        cases = (
            ("research.build", {"kind": "guide", "question": "q", "sources": []}),
            ("browser.click", {"selector": "#buy"}),
            ("system.copy_text", {"text": "secret"}),
        )
        for name, arguments in cases:
            definition = service._registry.get(name)  # type: ignore[attr-defined]
            assert definition is not None
            with pytest.raises(RealtimeAssistanceCapabilityError):
                service._tool_executor.execute(  # type: ignore[attr-defined]
                    definition,
                    scope,
                    arguments,
                )
    finally:
        service.close()
