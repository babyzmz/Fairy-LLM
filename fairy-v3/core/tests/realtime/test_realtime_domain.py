from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.realtime.models import (
    GameMemoryDigest,
    RealtimeMemoryMode,
    RealtimeProvider,
    RealtimeSession,
    RealtimeSessionStatus,
    RealtimeVoiceMode,
)


def _session() -> RealtimeSession:
    return RealtimeSession.create(
        device_id="test-device",
        idempotency_key="realtime-1",
        conversation_id=None,
        provider=RealtimeProvider.GLM_REALTIME_FLASH,
        model_id="glm-realtime-flash",
        voice_mode=RealtimeVoiceMode.NATIVE,
        memory_mode=RealtimeMemoryMode.PROGRESS_DIGEST,
        microphone_consent=True,
        screen_consent=True,
        game_audio_consent=False,
    )


def test_session_state_machine_rejects_illegal_transition() -> None:
    session = _session().transition_to(RealtimeSessionStatus.ACTIVE)
    with pytest.raises(InvalidTransitionError):
        session.transition_to(RealtimeSessionStatus.COMPLETED)

    completed = session.transition_to(RealtimeSessionStatus.STOPPING).transition_to(
        RealtimeSessionStatus.COMPLETED
    )
    assert completed.ended_at is not None
    assert completed.provider is RealtimeProvider.GLM_REALTIME_FLASH


def test_starting_session_can_be_cancelled_before_provider_activation() -> None:
    cancelled = _session().transition_to(RealtimeSessionStatus.CANCELLED)

    assert cancelled.status is RealtimeSessionStatus.CANCELLED
    assert cancelled.ended_at is not None


def test_game_audio_requires_selected_window_consent() -> None:
    with pytest.raises(ValueError, match="selected game window"):
        RealtimeSession.create(
            device_id="test-device",
            idempotency_key="realtime-game-audio-without-window",
            conversation_id=None,
            provider=RealtimeProvider.GLM_REALTIME_FLASH,
            model_id="glm-realtime-flash",
            voice_mode=RealtimeVoiceMode.NATIVE,
            memory_mode=RealtimeMemoryMode.PROGRESS_DIGEST,
            microphone_consent=True,
            screen_consent=False,
            game_audio_consent=True,
        )


def test_realtime_usage_is_monotonic() -> None:
    session = _session().with_usage(
        audio_input_ms=100,
        audio_output_ms=50,
        video_frame_count=3,
        interruption_count=2,
        tool_call_count=1,
    )
    with pytest.raises(ValueError, match="event counts cannot decrease"):
        session.with_usage(
            audio_input_ms=100,
            audio_output_ms=50,
            video_frame_count=3,
            interruption_count=1,
            tool_call_count=1,
        )


def test_game_memory_digest_is_small_and_bounded() -> None:
    digest = GameMemoryDigest.create(
        session_id=_session().id,
        game_title="Final Fantasy XIV",
        played_at=datetime.now(UTC),
        duration_seconds=1_200,
        activities=("Completed a dungeon", "Reached level 20"),
        progress_summary="Finished the main objective and unlocked the next area.",
        next_goal="Continue the main story.",
        accepted=True,
    )
    assert digest.encoded_size <= 2_048
    assert digest.accepted is True

    with pytest.raises(ValueError, match="at most 5"):
        GameMemoryDigest.create(
            session_id=_session().id,
            game_title="Game",
            played_at=datetime.now(UTC),
            duration_seconds=10,
            activities=("a", "b", "c", "d", "e", "f"),
            progress_summary="progress",
        )
