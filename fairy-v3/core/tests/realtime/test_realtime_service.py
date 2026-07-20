from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect

from fairy_core.domain.errors import InvalidTransitionError, VersionConflictError
from fairy_core.persistence import create_sqlite_core_engine
from fairy_core.transports.stdio import build_local_service


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
        assert service.invoke(
            "realtime.memories.delete", {"memory_id": memory["id"]}
        )["deleted"] is True
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
            column["name"]
            for column in inspector.get_columns("core_game_memory_observations")
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
