from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect

from fairy_core.domain.errors import InvalidTransitionError, VersionConflictError
from fairy_core.persistence import create_sqlite_core_engine
from fairy_core.realtime.repository import SqlAlchemyRealtimeRepository
from fairy_core.transports.stdio import build_local_service
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner


def test_realtime_session_and_game_memory_round_trip(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "auto",
                "locale": "zh-CN",
                "microphone_consent": True,
                "screen_consent": True,
                "game_audio_consent": False,
                "idempotency_key": "session-1",
            },
        )
        assert started["provider"] == "glm_realtime_flash"
        assert started["status"] == "starting"
        assert started["revision"] == 1

        replayed = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "auto",
                "locale": "zh-CN",
                "microphone_consent": True,
                "screen_consent": True,
                "game_audio_consent": False,
                "idempotency_key": "session-1",
            },
        )
        assert replayed["id"] == started["id"]

        active = service.invoke(
            "realtime.sessions.report",
            {
                "session_id": started["id"],
                "status": "active",
                "expected_revision": 1,
                "audio_input_ms": 500,
                "video_frame_count": 1,
            },
        )
        with pytest.raises(VersionConflictError):
            service.invoke(
                "realtime.sessions.stop",
                {"session_id": started["id"], "expected_revision": 1},
            )
        stopping = service.invoke(
            "realtime.sessions.stop",
            {"session_id": started["id"], "expected_revision": active["revision"]},
        )
        repeated_stop = service.invoke(
            "realtime.sessions.stop",
            {"session_id": started["id"], "expected_revision": active["revision"]},
        )
        assert repeated_stop == stopping
        completed = service.invoke(
            "realtime.sessions.report",
            {
                "session_id": started["id"],
                "status": "completed",
                "expected_revision": stopping["revision"],
                "audio_input_ms": 750,
                "audio_output_ms": 300,
                "video_frame_count": 2,
                "interruption_count": 1,
            },
        )
        assert completed["ended_at"] is not None
        terminal_stop = service.invoke(
            "realtime.sessions.stop",
            {"session_id": started["id"], "expected_revision": 1},
        )
        assert terminal_stop == completed

        memory = service.invoke(
            "realtime.memories.save",
            {
                "session_id": started["id"],
                "game_title": "A Test Game",
                "played_at": datetime.now(UTC).isoformat(),
                "duration_seconds": 60,
                "activities": ["Completed tutorial"],
                "progress_summary": "Finished the tutorial.",
                "next_goal": "Start chapter one.",
            },
        )
        assert memory["accepted"] is True
        assert service.invoke("realtime.memories.list", {})["items"][0]["id"] == memory["id"]
        assert (
            service.invoke("realtime.memories.delete", {"memory_id": memory["id"]})["deleted"]
            is True
        )
    finally:
        service.close()


def test_voice_start_links_a_scratch_conversation(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "auto",
                "locale": "en-AU",
                "microphone_consent": True,
                "screen_consent": True,
                "game_audio_consent": False,
                "idempotency_key": "voice-1",
            },
        )
        # Starting voice auto-links a fresh conversation.
        assert started["conversation_id"] is not None

        # An idempotent replay reuses the session and its conversation.
        replay = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "auto",
                "locale": "en-AU",
                "microphone_consent": True,
                "screen_consent": True,
                "game_audio_consent": False,
                "idempotency_key": "voice-1",
            },
        )
        assert replay["id"] == started["id"]
        assert replay["conversation_id"] == started["conversation_id"]

        # A new session gets its own conversation.
        other = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "auto",
                "locale": "en-AU",
                "microphone_consent": True,
                "screen_consent": True,
                "game_audio_consent": False,
                "idempotency_key": "voice-2",
            },
        )
        assert other["conversation_id"] is not None
        assert other["conversation_id"] != started["conversation_id"]
    finally:
        service.close()


def test_voice_start_rolls_back_scratch_state_when_session_save_fails(
    tmp_path, monkeypatch
) -> None:
    def fail_session_save(self, session):
        raise RuntimeError("injected realtime session failure")

    monkeypatch.setattr(
        SqlAlchemyRealtimeRepository,
        "add_session",
        fail_session_save,
    )
    service = build_local_service(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="injected realtime session failure"):
            service.invoke(
                "realtime.sessions.start",
                {
                    "device_id": "desktop-1",
                    "provider": "auto",
                    "locale": "en-AU",
                    "microphone_consent": True,
                    "screen_consent": True,
                    "game_audio_consent": False,
                    "idempotency_key": "voice-session-failure",
                },
            )

        assert service.invoke("conversations.list", {})["items"] == []
        assert service.invoke("realtime.sessions.list", {})["items"] == []
        _assert_no_scratch_artifacts(tmp_path)
    finally:
        service.close()


def test_voice_start_rolls_back_scratch_state_when_workspace_creation_fails(
    tmp_path, monkeypatch
) -> None:
    original_create = FileSystemWorkspaceProvisioner.create_initial_version

    def fail_after_workspace_creation(self, project_id, version_id, *, source=None):
        original_create(self, project_id, version_id, source=source)
        raise RuntimeError("injected workspace creation failure")

    monkeypatch.setattr(
        FileSystemWorkspaceProvisioner,
        "create_initial_version",
        fail_after_workspace_creation,
    )
    service = build_local_service(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="injected workspace creation failure"):
            service.invoke(
                "realtime.sessions.start",
                {
                    "device_id": "desktop-1",
                    "provider": "auto",
                    "locale": "en-AU",
                    "microphone_consent": True,
                    "screen_consent": True,
                    "game_audio_consent": False,
                    "idempotency_key": "voice-workspace-failure",
                },
            )

        assert service.invoke("conversations.list", {})["items"] == []
        assert service.invoke("realtime.sessions.list", {})["items"] == []
        _assert_no_scratch_artifacts(tmp_path)
    finally:
        service.close()


def test_voice_transcript_append_and_list(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "auto",
                "locale": "en-AU",
                "microphone_consent": True,
                "screen_consent": True,
                "game_audio_consent": False,
                "idempotency_key": "transcript-1",
            },
        )
        conversation_id = started["conversation_id"]
        assert conversation_id is not None

        first = service.invoke(
            "realtime.transcript.append",
            {"session_id": started["id"], "speaker": "user", "text": "Where is the boss?"},
        )
        second = service.invoke(
            "realtime.transcript.append",
            {"session_id": started["id"], "speaker": "assistant", "text": "Behind the door."},
        )
        # Sequences are server-allocated and monotonic, linked to the conversation.
        assert (first["sequence"], second["sequence"]) == (1, 2)
        assert first["conversation_id"] == conversation_id

        page = service.invoke(
            "realtime.transcript.list", {"conversation_id": conversation_id}
        )
        assert [(e["speaker"], e["text"]) for e in page["items"]] == [
            ("user", "Where is the boss?"),
            ("assistant", "Behind the door."),
        ]
        transcript_events = [
            event
            for event in service.invoke(
                "events.list", {"cursor": 0, "limit": 100}
            )["items"]
            if event["event_type"] == "realtime.transcript.appended"
        ]
        assert [event["conversation_id"] for event in transcript_events] == [
            conversation_id,
            conversation_id,
        ]
        assert [event["payload"] for event in transcript_events] == [
            {
                "session_id": started["id"],
                "conversation_id": conversation_id,
                "entry_id": first["id"],
                "sequence": 1,
            },
            {
                "session_id": started["id"],
                "conversation_id": conversation_id,
                "entry_id": second["id"],
                "sequence": 2,
            },
        ]
        assert "Where is the boss?" not in str(transcript_events)
        assert "Behind the door." not in str(transcript_events)

        # An unrelated conversation has no transcript.
        empty = service.invoke(
            "realtime.transcript.list",
            {"conversation_id": "01900000-0000-7000-8000-0000000000ff"},
        )
        assert empty["items"] == []

        # Appending against an unknown session is rejected, never silently dropped.
        with pytest.raises(KeyError):
            service.invoke(
                "realtime.transcript.append",
                {
                    "session_id": "01900000-0000-7000-8000-0000000000aa",
                    "speaker": "user",
                    "text": "Nobody is listening.",
                },
            )
    finally:
        service.close()


def test_starting_session_stop_is_cancelled_and_idempotent(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "glm_realtime_flash",
                "microphone_consent": True,
                "screen_consent": True,
                "idempotency_key": "session-cancel-starting",
            },
        )

        cancelled = service.invoke(
            "realtime.sessions.stop",
            {"session_id": started["id"], "expected_revision": started["revision"]},
        )
        replayed = service.invoke(
            "realtime.sessions.stop",
            {"session_id": started["id"], "expected_revision": started["revision"]},
        )

        assert cancelled["status"] == "cancelled"
        assert cancelled["ended_at"] is not None
        assert replayed == cancelled
    finally:
        service.close()


def test_memory_cannot_be_saved_while_session_is_active(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "gemini_live",
                "microphone_consent": True,
                "idempotency_key": "session-active",
            },
        )
        with pytest.raises(InvalidTransitionError):
            service.invoke(
                "realtime.memories.save",
                {
                    "session_id": started["id"],
                    "game_title": "A Test Game",
                    "played_at": datetime.now(UTC).isoformat(),
                    "duration_seconds": 10,
                    "activities": [],
                    "progress_summary": "Not finished.",
                },
            )
    finally:
        service.close()


def test_realtime_persistence_has_no_raw_context_columns(tmp_path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "privacy.db")
    try:
        inspector = inspect(engine)
        session_columns = {
            column["name"] for column in inspector.get_columns("core_realtime_sessions")
        }
        memory_columns = {
            column["name"] for column in inspector.get_columns("core_game_memory_observations")
        }
        forbidden = {
            "transcript",
            "audio",
            "audio_blob",
            "video",
            "frame",
            "frames",
            "provider_context",
            "conversation_items",
            "vad_events",
            "reasoning",
        }
        assert session_columns.isdisjoint(forbidden)
        assert memory_columns.isdisjoint(forbidden)
    finally:
        engine.dispose()


def _assert_no_scratch_artifacts(data_dir: Path) -> None:
    engine = create_sqlite_core_engine(data_dir / "core.db")
    try:
        with engine.connect() as connection:
            for table in (
                "core_conversations",
                "core_workspaces",
                "core_versions",
                "core_realtime_sessions",
            ):
                count = connection.exec_driver_sql(
                    f"SELECT COUNT(*) FROM {table}"
                ).scalar_one()
                assert count == 0, table
    finally:
        engine.dispose()
    projects_root = data_dir / "workspaces" / "projects"
    assert not projects_root.exists() or not any(projects_root.iterdir())
