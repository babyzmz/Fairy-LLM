from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import UUID

from fairy_core.assistant.schedule_models import AssistantOccurrenceStatus
from fairy_core.providers import CancellationToken, ModelDelta, ModelRequest, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider, wait_for_turn
from tests.assistant.test_application import _scratch_task


class _SerialProvider(ScriptedProvider):
    def __init__(self) -> None:
        super().__init__([])
        self.first_started = Event()
        self.release_first = Event()

    def stream(self, request: ModelRequest, cancellation: CancellationToken):
        self.requests.append(request)
        if len(self.requests) == 1:
            self.first_started.set()
            while not self.release_first.wait(0.01):
                cancellation.raise_if_cancelled()
            text = "Foreground result"
        else:
            text = "Scheduled result"
        yield ModelDelta.text(profile_id="scripted", sequence=1, text=text)
        yield ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="stop")


def test_run_now_creates_one_normal_message_turn_and_workflow(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            (
                ModelDelta.text(
                    profile_id="scripted",
                    sequence=1,
                    text="Scheduled result",
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            )
        ]
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        task = _scratch_task(service, "Prepare a conversation for scheduling")
        schedule = service.invoke(
            "assistant.schedules.create",
            {
                "conversation_id": task["conversation_id"],
                "instruction": "Report the scheduled status",
                "operation_mode": "answer",
                "trigger_kind": "daily",
                "trigger_rule": {"local_time": "09:00"},
                "timezone": "Australia/Sydney",
                "next_fire_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "profile_id": "scripted",
                "idempotency_key": "schedule:service:one",
            },
        )
        assert (
            service.invoke(
                "messages.list",
                {"conversation_id": task["conversation_id"]},
            )["items"]
            == []
        )

        occurrence = service.invoke(
            "assistant.schedules.run_now",
            {
                "schedule_id": schedule["id"],
                "expected_revision": schedule["active_revision"],
                "idempotency_key": "schedule:service:run-now",
            },
        )
        turn_id = _wait_for_occurrence_turn(service, UUID(occurrence["id"]))
        completed = wait_for_turn(service, str(turn_id))
        settled = _wait_for_occurrence_status(
            service,
            UUID(occurrence["id"]),
            AssistantOccurrenceStatus.SUCCEEDED,
        )
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]

        assert completed["workflow_run_id"] is not None
        assert settled.completed_at is not None
        assert [(message["role"], message["content"]) for message in messages] == [
            ("user", "Report the scheduled status"),
            ("assistant", "Scheduled result"),
        ]
        replay = service.invoke(
            "assistant.schedules.run_now",
            {
                "schedule_id": schedule["id"],
                "expected_revision": schedule["active_revision"],
                "idempotency_key": "schedule:service:run-now",
            },
        )
        assert replay["id"] == occurrence["id"]
        assert len(provider.requests) == 1
    finally:
        service.close()


def test_pending_schedule_waits_for_the_active_turn_in_the_same_chat(tmp_path: Path) -> None:
    provider = _SerialProvider()
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        task = _scratch_task(service, "Foreground request")
        foreground = service.invoke(
            "assistant.turns.create",
            {
                "task_id": task["id"],
                "profile_id": "scripted",
                "idempotency_key": "turn:foreground-before-schedule",
            },
        )
        service.invoke("assistant.turns.start", {"turn_id": foreground["id"]})
        assert provider.first_started.wait(3)
        schedule = service.invoke(
            "assistant.schedules.create",
            {
                "conversation_id": task["conversation_id"],
                "instruction": "Run after the foreground request",
                "operation_mode": "answer",
                "trigger_kind": "once",
                "trigger_rule": {},
                "timezone": "Australia/Sydney",
                "next_fire_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "profile_id": "scripted",
                "idempotency_key": "schedule:serial-chat",
            },
        )
        occurrence = service.invoke(
            "assistant.schedules.run_now",
            {
                "schedule_id": schedule["id"],
                "expected_revision": schedule["active_revision"],
                "idempotency_key": "schedule:serial-chat:run",
            },
        )
        time.sleep(0.2)
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            pending = unit_of_work.assistant_schedules.get_occurrence(UUID(occurrence["id"]))
        assert pending is not None
        assert pending.status is AssistantOccurrenceStatus.PENDING
        assert len(provider.requests) == 1

        provider.release_first.set()
        wait_for_turn(service, foreground["id"])
        scheduled_turn_id = _wait_for_occurrence_turn(service, UUID(occurrence["id"]))
        wait_for_turn(service, str(scheduled_turn_id))

        assert len(provider.requests) == 2
        assert [request.messages[-1].content for request in provider.requests] == [
            "Foreground request",
            "Run after the foreground request",
        ]
    finally:
        provider.release_first.set()
        service.close()


def test_changed_permission_snapshot_pauses_before_creating_a_message(tmp_path: Path) -> None:
    provider = ScriptedProvider([])
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        task = _scratch_task(service, "Prepare permission-bound schedule")
        schedule = service.invoke(
            "assistant.schedules.create",
            {
                "conversation_id": task["conversation_id"],
                "instruction": "This must wait for permission confirmation",
                "operation_mode": "answer",
                "trigger_kind": "daily",
                "trigger_rule": {"local_time": "09:00"},
                "timezone": "Australia/Sydney",
                "next_fire_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "profile_id": "scripted",
                "idempotency_key": "schedule:permission-snapshot",
            },
        )
        settings = service.invoke("permissions.get", {})
        service.invoke(
            "permissions.update",
            {
                "profile": "observe",
                "capability_overrides": settings["capability_overrides"],
                "expected_revision": settings["revision"],
                "idempotency_key": "permissions:observe-for-schedule-test",
            },
        )
        occurrence = service.invoke(
            "assistant.schedules.run_now",
            {
                "schedule_id": schedule["id"],
                "expected_revision": schedule["active_revision"],
                "idempotency_key": "schedule:permission-snapshot:run",
            },
        )

        _wait_for_occurrence_status(
            service,
            UUID(occurrence["id"]),
            AssistantOccurrenceStatus.ATTENTION_REQUIRED,
        )
        paused = service.invoke(
            "assistant.schedules.get",
            {"schedule_id": schedule["id"]},
        )
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]

        assert paused["status"] == "paused"
        assert paused["attention_code"] == "PERMISSION_PROFILE_CHANGED"
        assert messages == []
        assert provider.requests == []
    finally:
        service.close()


def test_schedule_management_only_changes_future_definition(tmp_path: Path) -> None:
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((ScriptedProvider([]),)),
    )
    try:
        task = _scratch_task(service, "Prepare schedule management")
        initial_fire = datetime.now(UTC) + timedelta(days=2)
        schedule = service.invoke(
            "assistant.schedules.create",
            {
                "conversation_id": task["conversation_id"],
                "instruction": "Original instruction",
                "operation_mode": "answer",
                "trigger_kind": "daily",
                "trigger_rule": {"local_time": "09:00"},
                "timezone": "Australia/Sydney",
                "next_fire_at": initial_fire.isoformat(),
                "profile_id": "scripted",
                "idempotency_key": "schedule:management",
            },
        )
        revised_fire = initial_fire + timedelta(days=1)
        updated = service.invoke(
            "assistant.schedules.update",
            {
                "schedule_id": schedule["id"],
                "expected_revision": 1,
                "instruction": "Revised instruction",
                "operation_mode": "create_new_version",
                "trigger_kind": "weekdays",
                "trigger_rule": {"local_time": "10:30"},
                "timezone": "Australia/Sydney",
                "next_fire_at": revised_fire.isoformat(),
            },
        )
        paused = service.invoke(
            "assistant.schedules.pause",
            {"schedule_id": schedule["id"], "expected_revision": 2},
        )
        resumed = service.invoke(
            "assistant.schedules.resume",
            {"schedule_id": schedule["id"], "expected_revision": 2},
        )
        cancelled = service.invoke(
            "assistant.schedules.cancel",
            {"schedule_id": schedule["id"], "expected_revision": 2},
        )
        listed = service.invoke(
            "assistant.schedules.list",
            {"conversation_id": task["conversation_id"], "statuses": ["cancelled"]},
        )["items"]

        assert updated["active_revision"] == 2
        assert updated["instruction"] == "Revised instruction"
        assert updated["operation_mode"] == "create_new_version"
        assert paused["status"] == "paused"
        assert resumed["status"] == "active"
        assert cancelled["status"] == "cancelled"
        assert [item["id"] for item in listed] == [schedule["id"]]
    finally:
        service.close()


def test_background_task_projection_groups_schedules_by_current_chat(tmp_path: Path) -> None:
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((ScriptedProvider([]),)),
    )
    try:
        current_task = _scratch_task(service, "Current scheduled chat")
        other_task = _scratch_task(service, "Other scheduled chat")
        next_fire = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        for index, task in enumerate((current_task, other_task), start=1):
            service.invoke(
                "assistant.schedules.create",
                {
                    "conversation_id": task["conversation_id"],
                    "instruction": f"Scheduled instruction {index}",
                    "operation_mode": "answer",
                    "trigger_kind": "daily",
                    "trigger_rule": {"local_time": "09:00"},
                    "timezone": "Australia/Sydney",
                    "next_fire_at": next_fire,
                    "profile_id": "scripted",
                    "idempotency_key": f"schedule:background:{index}",
                },
            )

        page = service.invoke(
            "assistant.background_tasks.list",
            {"current_conversation_id": current_task["conversation_id"]},
        )

        assert page["nonterminal_count"] == 2
        assert [item["title"] for item in page["current"]] == ["Scheduled instruction 1"]
        assert [item["title"] for item in page["other"]] == ["Scheduled instruction 2"]
        assert page["current"][0]["status"] == "scheduled"
        assert page["current"][0]["can_pause"] is True
        assert page["current"][0]["can_run_now"] is True
        assert page["recent"] == []
    finally:
        service.close()


def _wait_for_occurrence_turn(service, occurrence_id: UUID) -> UUID:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            occurrence = unit_of_work.assistant_schedules.get_occurrence(occurrence_id)
        if (
            occurrence is not None
            and occurrence.status is AssistantOccurrenceStatus.DISPATCHED
            and occurrence.turn_id is not None
        ):
            return occurrence.turn_id
        time.sleep(0.02)
    raise AssertionError("Scheduled occurrence did not dispatch")


def _wait_for_occurrence_status(
    service,
    occurrence_id: UUID,
    status: AssistantOccurrenceStatus,
):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            occurrence = unit_of_work.assistant_schedules.get_occurrence(occurrence_id)
        if occurrence is not None and occurrence.status is status:
            return occurrence
        time.sleep(0.02)
    raise AssertionError(f"Scheduled occurrence did not reach {status.value}")
