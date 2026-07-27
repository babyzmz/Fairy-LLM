from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.ids import new_id
from fairy_core.realtime.models import (
    GameMemoryDigest,
    RealtimeAssistance,
    RealtimeAssistanceCitation,
    RealtimeAssistanceStatus,
    RealtimeCaptionSpeaker,
    RealtimeMemoryMode,
    RealtimeProvider,
    RealtimeSession,
    RealtimeSessionStatus,
    RealtimeTranscriptEntry,
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


def test_local_provider_is_a_stable_historical_audit_value() -> None:
    session = RealtimeSession.create(
        device_id="test-device",
        idempotency_key="local-audit-1",
        conversation_id=None,
        provider=RealtimeProvider.LOCAL_MINI_CPM_O45,
        model_id="openbmb/minicpm-o-4.5-fairy-beta@4.5-q4-502eec5",
        voice_mode=RealtimeVoiceMode.FAIRY,
        memory_mode=RealtimeMemoryMode.NONE,
        microphone_consent=True,
        screen_consent=True,
        game_audio_consent=False,
    )
    assert session.provider.value == "local_mini_cpm_o45"


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


def test_realtime_transcript_entry_normalizes_and_bounds_text() -> None:
    entry = RealtimeTranscriptEntry.create(
        session_id=new_id(),
        conversation_id=new_id(),
        sequence=1,
        speaker=RealtimeCaptionSpeaker.ASSISTANT,
        text="  The boss   is\n  weak to fire.  ",
    )
    assert entry.text == "The boss is weak to fire."
    assert entry.speaker is RealtimeCaptionSpeaker.ASSISTANT

    with pytest.raises(ValueError, match="sequence must be positive"):
        RealtimeTranscriptEntry.create(
            session_id=new_id(),
            conversation_id=new_id(),
            sequence=0,
            speaker=RealtimeCaptionSpeaker.USER,
            text="Anything.",
        )

    with pytest.raises(ValueError, match="1 to 4000 characters"):
        RealtimeTranscriptEntry.create(
            session_id=new_id(),
            conversation_id=new_id(),
            sequence=1,
            speaker=RealtimeCaptionSpeaker.USER,
            text="   ",
        )

    with pytest.raises(ValueError, match="1 to 4000 characters"):
        RealtimeTranscriptEntry.create(
            session_id=new_id(),
            conversation_id=new_id(),
            sequence=1,
            speaker=RealtimeCaptionSpeaker.USER,
            text="x" * 4_001,
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


def test_realtime_assistance_normalizes_and_transitions_without_raw_context() -> None:
    assistance = RealtimeAssistance.create(
        session_id=new_id(),
        conversation_id=new_id(),
        request_id=" guide-1 ",
        segment_id=" segment-1 ",
        context_epoch=2,
        question="  Where is the hidden boss? ",
        activity_profile="game",
        application_title="Test Game",
        observed_facts=(" Map is open ", "Player asked for help"),
        allow_network=True,
        locale="en-AU",
    )

    assert assistance.status is RealtimeAssistanceStatus.QUEUED
    assert assistance.question == "Where is the hidden boss?"
    assert assistance.observed_facts == ("Map is open", "Player asked for help")
    assert len(assistance.request_fingerprint) == 64

    task_id = new_id()
    turn_id = new_id()
    running = assistance.start(task_id=task_id, turn_id=turn_id)
    awaiting = running.await_approval()
    resumed = awaiting.resume()
    completed = resumed.complete(
        message_id=new_id(),
        spoken_summary="The boss is behind the eastern gate.",
        display_markdown="The boss is behind the **eastern gate**.",
        citations=(
            RealtimeAssistanceCitation.create(
                title="Official guide",
                url="https://example.invalid/guide",
            ),
        ),
        freshness="checked_at_request_time",
    )

    assert completed.status is RealtimeAssistanceStatus.COMPLETED
    assert completed.task_id == task_id
    assert completed.turn_id == turn_id
    assert completed.requires_user_confirmation is False
    assert completed.revision == 5
    with pytest.raises(InvalidTransitionError):
        completed.cancel()


def test_realtime_assistance_request_fingerprint_detects_changed_input() -> None:
    session_id = new_id()
    conversation_id = new_id()
    base = dict(
        session_id=session_id,
        conversation_id=conversation_id,
        request_id="guide-1",
        segment_id="segment-1",
        context_epoch=1,
        question="Where is the boss?",
        activity_profile="game",
        application_title="Test Game",
        observed_facts=(),
        allow_network=True,
        locale="en-AU",
    )
    first = RealtimeAssistance.create(**base)
    replay = RealtimeAssistance.create(**base)
    changed = RealtimeAssistance.create(**{**base, "question": "Where is the chest?"})

    assert first.same_request(replay)
    assert not first.same_request(changed)


def test_realtime_assistance_bounds_public_projection() -> None:
    with pytest.raises(ValueError, match="at most 16"):
        RealtimeAssistance.create(
            session_id=new_id(),
            conversation_id=new_id(),
            request_id="guide-1",
            segment_id="segment-1",
            context_epoch=1,
            question="Question",
            activity_profile="game",
            application_title=None,
            observed_facts=tuple(f"fact-{index}" for index in range(17)),
            allow_network=False,
            locale="en-AU",
        )
