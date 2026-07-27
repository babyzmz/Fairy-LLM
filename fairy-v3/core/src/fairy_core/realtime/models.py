from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.ids import new_id


class RealtimeProvider(StrEnum):
    LOCAL_MINI_CPM_O45 = "local_mini_cpm_o45"
    GEMINI_LIVE = "gemini_live"
    GLM_REALTIME_FLASH = "glm_realtime_flash"
    GLM_REALTIME_AIR = "glm_realtime_air"


class RealtimeVoiceMode(StrEnum):
    NATIVE = "native"
    FAIRY = "fairy"


class RealtimeMemoryMode(StrEnum):
    PROGRESS_DIGEST = "progress_digest"
    NONE = "none"


class RealtimeCaptionSpeaker(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


_MAX_TRANSCRIPT_TEXT = 4_000


class RealtimeSessionStatus(StrEnum):
    STARTING = "starting"
    ACTIVE = "active"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


_SESSION_TRANSITIONS: Mapping[RealtimeSessionStatus, frozenset[RealtimeSessionStatus]] = (
    MappingProxyType(
        {
            RealtimeSessionStatus.STARTING: frozenset(
                {
                    RealtimeSessionStatus.ACTIVE,
                    RealtimeSessionStatus.FAILED,
                    RealtimeSessionStatus.CANCELLED,
                    RealtimeSessionStatus.INTERRUPTED,
                }
            ),
            RealtimeSessionStatus.ACTIVE: frozenset(
                {
                    RealtimeSessionStatus.STOPPING,
                    RealtimeSessionStatus.FAILED,
                    RealtimeSessionStatus.INTERRUPTED,
                }
            ),
            RealtimeSessionStatus.STOPPING: frozenset(
                {
                    RealtimeSessionStatus.COMPLETED,
                    RealtimeSessionStatus.FAILED,
                    RealtimeSessionStatus.INTERRUPTED,
                }
            ),
            RealtimeSessionStatus.COMPLETED: frozenset(),
            RealtimeSessionStatus.FAILED: frozenset(),
            RealtimeSessionStatus.CANCELLED: frozenset(),
            RealtimeSessionStatus.INTERRUPTED: frozenset(),
        }
    )
)


def _now() -> datetime:
    return datetime.now(UTC)


def _clean_text(value: str, *, name: str, maximum: int) -> str:
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} must contain 1 to {maximum} characters")
    return normalized


@dataclass(frozen=True, slots=True)
class RealtimeSession:
    id: UUID
    idempotency_key: str
    device_id: str
    conversation_id: UUID | None
    provider: RealtimeProvider
    model_id: str
    voice_mode: RealtimeVoiceMode
    memory_mode: RealtimeMemoryMode
    status: RealtimeSessionStatus
    microphone_consent: bool
    screen_consent: bool
    game_audio_consent: bool
    audio_input_ms: int
    audio_output_ms: int
    video_frame_count: int
    interruption_count: int
    tool_call_count: int
    last_error_code: str | None
    started_at: datetime
    ended_at: datetime | None
    revision: int

    @classmethod
    def create(
        cls,
        *,
        device_id: str,
        idempotency_key: str,
        conversation_id: UUID | None,
        provider: RealtimeProvider,
        model_id: str,
        voice_mode: RealtimeVoiceMode,
        memory_mode: RealtimeMemoryMode,
        microphone_consent: bool,
        screen_consent: bool,
        game_audio_consent: bool,
        now: datetime | None = None,
    ) -> RealtimeSession:
        normalized_device = _clean_text(device_id, name="device_id", maximum=128)
        normalized_key = _clean_text(idempotency_key, name="idempotency_key", maximum=512)
        normalized_model = _clean_text(model_id, name="model_id", maximum=128)
        if not microphone_consent:
            raise ValueError("microphone consent is required for a realtime session")
        if game_audio_consent and not screen_consent:
            raise ValueError("game audio requires the selected game window")
        return cls(
            id=new_id(),
            idempotency_key=normalized_key,
            device_id=normalized_device,
            conversation_id=conversation_id,
            provider=provider,
            model_id=normalized_model,
            voice_mode=voice_mode,
            memory_mode=memory_mode,
            status=RealtimeSessionStatus.STARTING,
            microphone_consent=True,
            screen_consent=screen_consent,
            game_audio_consent=game_audio_consent,
            audio_input_ms=0,
            audio_output_ms=0,
            video_frame_count=0,
            interruption_count=0,
            tool_call_count=0,
            last_error_code=None,
            started_at=(now or _now()).astimezone(UTC),
            ended_at=None,
            revision=1,
        )

    def transition_to(
        self,
        status: RealtimeSessionStatus,
        *,
        error_code: str | None = None,
        ended_at: datetime | None = None,
    ) -> RealtimeSession:
        if status not in _SESSION_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"cannot transition RealtimeSession from {self.status} to {status}"
            )
        terminal = status in {
            RealtimeSessionStatus.COMPLETED,
            RealtimeSessionStatus.FAILED,
            RealtimeSessionStatus.CANCELLED,
            RealtimeSessionStatus.INTERRUPTED,
        }
        if status is RealtimeSessionStatus.FAILED:
            error_code = _clean_text(
                error_code or "REALTIME_PROVIDER_FAILED",
                name="error_code",
                maximum=128,
            )
        elif error_code is not None:
            raise ValueError("only failed sessions may carry an error code")
        return replace(
            self,
            status=status,
            last_error_code=error_code,
            ended_at=(ended_at or _now()).astimezone(UTC) if terminal else None,
            revision=self.revision + 1,
        )

    def with_usage(
        self,
        *,
        audio_input_ms: int,
        audio_output_ms: int,
        video_frame_count: int,
        interruption_count: int,
        tool_call_count: int,
    ) -> RealtimeSession:
        values = (
            audio_input_ms,
            audio_output_ms,
            video_frame_count,
            interruption_count,
            tool_call_count,
        )
        if any(value < 0 for value in values):
            raise ValueError("realtime usage values cannot be negative")
        if audio_input_ms < self.audio_input_ms or audio_output_ms < self.audio_output_ms:
            raise ValueError("realtime audio usage cannot decrease")
        if video_frame_count < self.video_frame_count:
            raise ValueError("realtime video usage cannot decrease")
        if interruption_count < self.interruption_count or tool_call_count < self.tool_call_count:
            raise ValueError("realtime event counts cannot decrease")
        return replace(
            self,
            audio_input_ms=audio_input_ms,
            audio_output_ms=audio_output_ms,
            video_frame_count=video_frame_count,
            interruption_count=interruption_count,
            tool_call_count=tool_call_count,
            revision=self.revision + 1,
        )


@dataclass(frozen=True, slots=True)
class GameMemoryDigest:
    id: UUID
    session_id: UUID
    game_title: str
    played_at: datetime
    duration_seconds: int
    activities: tuple[str, ...]
    progress_summary: str
    next_goal: str | None
    notable_outcome: str | None
    accepted: bool
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        session_id: UUID,
        game_title: str,
        played_at: datetime,
        duration_seconds: int,
        activities: tuple[str, ...],
        progress_summary: str,
        next_goal: str | None = None,
        notable_outcome: str | None = None,
        accepted: bool = False,
        now: datetime | None = None,
    ) -> GameMemoryDigest:
        if duration_seconds < 0 or duration_seconds > 86_400:
            raise ValueError("duration_seconds must be between 0 and 86400")
        if len(activities) > 5:
            raise ValueError("a game memory digest supports at most 5 activities")
        normalized_activities = tuple(
            _clean_text(item, name="activity", maximum=160) for item in activities
        )
        digest = cls(
            id=new_id(),
            session_id=session_id,
            game_title=_clean_text(game_title, name="game_title", maximum=160),
            played_at=played_at.astimezone(UTC),
            duration_seconds=duration_seconds,
            activities=normalized_activities,
            progress_summary=_clean_text(progress_summary, name="progress_summary", maximum=800),
            next_goal=(
                _clean_text(next_goal, name="next_goal", maximum=300)
                if next_goal is not None
                else None
            ),
            notable_outcome=(
                _clean_text(notable_outcome, name="notable_outcome", maximum=300)
                if notable_outcome is not None
                else None
            ),
            accepted=accepted,
            created_at=(now or _now()).astimezone(UTC),
        )
        if digest.encoded_size > 2_048:
            raise ValueError("game memory digest exceeds the 2 KiB persistence limit")
        return digest

    @property
    def encoded_size(self) -> int:
        payload = {
            "game_title": self.game_title,
            "played_at": self.played_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "activities": self.activities,
            "progress_summary": self.progress_summary,
            "next_goal": self.next_goal,
            "notable_outcome": self.notable_outcome,
        }
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def accept(self) -> GameMemoryDigest:
        return self if self.accepted else replace(self, accepted=True)


@dataclass(frozen=True, slots=True)
class RealtimeTranscriptEntry:
    id: UUID
    session_id: UUID
    conversation_id: UUID
    sequence: int
    speaker: RealtimeCaptionSpeaker
    text: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        session_id: UUID,
        conversation_id: UUID,
        sequence: int,
        speaker: RealtimeCaptionSpeaker,
        text: str,
        now: datetime | None = None,
    ) -> RealtimeTranscriptEntry:
        if isinstance(sequence, bool) or sequence < 1:
            raise ValueError("transcript sequence must be positive")
        normalized = " ".join(text.split())
        if not normalized or len(normalized) > _MAX_TRANSCRIPT_TEXT:
            raise ValueError(
                f"transcript text must contain 1 to {_MAX_TRANSCRIPT_TEXT} characters"
            )
        return cls(
            id=new_id(),
            session_id=session_id,
            conversation_id=conversation_id,
            sequence=sequence,
            speaker=speaker,
            text=normalized,
            created_at=(now or _now()).astimezone(UTC),
        )


__all__ = [
    "GameMemoryDigest",
    "RealtimeCaptionSpeaker",
    "RealtimeMemoryMode",
    "RealtimeProvider",
    "RealtimeSession",
    "RealtimeSessionStatus",
    "RealtimeTranscriptEntry",
    "RealtimeVoiceMode",
]
