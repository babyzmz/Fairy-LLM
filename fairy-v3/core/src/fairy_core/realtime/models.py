from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
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


class RealtimeAssistanceStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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

_ASSISTANCE_TRANSITIONS: Mapping[RealtimeAssistanceStatus, frozenset[RealtimeAssistanceStatus]] = (
    MappingProxyType(
        {
            RealtimeAssistanceStatus.QUEUED: frozenset(
                {
                    RealtimeAssistanceStatus.RUNNING,
                    RealtimeAssistanceStatus.FAILED,
                    RealtimeAssistanceStatus.CANCELLED,
                }
            ),
            RealtimeAssistanceStatus.RUNNING: frozenset(
                {
                    RealtimeAssistanceStatus.AWAITING_APPROVAL,
                    RealtimeAssistanceStatus.COMPLETED,
                    RealtimeAssistanceStatus.FAILED,
                    RealtimeAssistanceStatus.CANCELLED,
                }
            ),
            RealtimeAssistanceStatus.AWAITING_APPROVAL: frozenset(
                {
                    RealtimeAssistanceStatus.RUNNING,
                    RealtimeAssistanceStatus.COMPLETED,
                    RealtimeAssistanceStatus.FAILED,
                    RealtimeAssistanceStatus.CANCELLED,
                }
            ),
            RealtimeAssistanceStatus.COMPLETED: frozenset(),
            RealtimeAssistanceStatus.FAILED: frozenset(),
            RealtimeAssistanceStatus.CANCELLED: frozenset(),
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
            raise ValueError(f"transcript text must contain 1 to {_MAX_TRANSCRIPT_TEXT} characters")
        return cls(
            id=new_id(),
            session_id=session_id,
            conversation_id=conversation_id,
            sequence=sequence,
            speaker=speaker,
            text=normalized,
            created_at=(now or _now()).astimezone(UTC),
        )


@dataclass(frozen=True, slots=True)
class RealtimeAssistanceCitation:
    title: str
    url: str

    @classmethod
    def create(cls, *, title: str, url: str) -> RealtimeAssistanceCitation:
        return cls(
            title=_clean_text(title, name="citation title", maximum=300),
            url=_clean_text(url, name="citation url", maximum=4_096),
        )


@dataclass(frozen=True, slots=True)
class RealtimeAssistance:
    id: UUID
    session_id: UUID
    conversation_id: UUID
    request_id: str
    request_fingerprint: str
    segment_id: str
    context_epoch: int
    question: str
    activity_profile: str
    application_title: str | None
    observed_facts: tuple[str, ...]
    allow_network: bool
    locale: str
    status: RealtimeAssistanceStatus
    task_id: UUID | None
    turn_id: UUID | None
    message_id: UUID | None
    spoken_summary: str | None
    display_markdown: str | None
    citations: tuple[RealtimeAssistanceCitation, ...]
    freshness: str | None
    requires_user_confirmation: bool
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    revision: int

    @classmethod
    def create(
        cls,
        *,
        session_id: UUID,
        conversation_id: UUID,
        request_id: str,
        segment_id: str,
        context_epoch: int,
        question: str,
        activity_profile: str,
        application_title: str | None,
        observed_facts: tuple[str, ...],
        allow_network: bool,
        locale: str,
        now: datetime | None = None,
    ) -> RealtimeAssistance:
        if isinstance(context_epoch, bool) or context_epoch < 1:
            raise ValueError("context_epoch must be positive")
        if len(observed_facts) > 16:
            raise ValueError("observed_facts supports at most 16 items")
        normalized_session_id = UUID(str(session_id))
        normalized_conversation_id = UUID(str(conversation_id))
        normalized_request_id = _clean_text(request_id, name="request_id", maximum=128)
        normalized_segment_id = _clean_text(segment_id, name="segment_id", maximum=128)
        normalized_question = _clean_text(question, name="question", maximum=4_000)
        normalized_activity = _clean_text(activity_profile, name="activity_profile", maximum=32)
        normalized_application = (
            _clean_text(application_title, name="application_title", maximum=128)
            if application_title is not None
            else None
        )
        normalized_facts = tuple(
            _clean_text(value, name="observed_fact", maximum=300) for value in observed_facts
        )
        normalized_locale = _clean_text(locale, name="locale", maximum=32)
        canonical = {
            "session_id": str(normalized_session_id),
            "conversation_id": str(normalized_conversation_id),
            "request_id": normalized_request_id,
            "segment_id": normalized_segment_id,
            "context_epoch": context_epoch,
            "question": normalized_question,
            "activity_profile": normalized_activity,
            "application_title": normalized_application,
            "observed_facts": normalized_facts,
            "allow_network": bool(allow_network),
            "locale": normalized_locale,
        }
        fingerprint = sha256(
            json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        timestamp = (now or _now()).astimezone(UTC)
        return cls(
            id=new_id(),
            session_id=normalized_session_id,
            conversation_id=normalized_conversation_id,
            request_id=normalized_request_id,
            segment_id=normalized_segment_id,
            context_epoch=context_epoch,
            question=normalized_question,
            activity_profile=normalized_activity,
            application_title=normalized_application,
            observed_facts=normalized_facts,
            allow_network=bool(allow_network),
            locale=normalized_locale,
            request_fingerprint=fingerprint,
            status=RealtimeAssistanceStatus.QUEUED,
            task_id=None,
            turn_id=None,
            message_id=None,
            spoken_summary=None,
            display_markdown=None,
            citations=(),
            freshness=None,
            requires_user_confirmation=False,
            error_code=None,
            created_at=timestamp,
            updated_at=timestamp,
            revision=1,
        )

    def same_request(self, other: RealtimeAssistance) -> bool:
        return self.request_fingerprint == other.request_fingerprint

    def with_queue_reason(
        self,
        error_code: str | None,
        *,
        now: datetime | None = None,
    ) -> RealtimeAssistance:
        if self.status is not RealtimeAssistanceStatus.QUEUED:
            raise InvalidTransitionError("only queued Realtime Assistance has a queue reason")
        normalized = (
            _clean_text(error_code, name="error_code", maximum=128)
            if error_code is not None
            else None
        )
        if normalized == self.error_code:
            return self
        return replace(
            self,
            error_code=normalized,
            updated_at=(now or _now()).astimezone(UTC),
            revision=self.revision + 1,
        )

    def start(
        self,
        *,
        task_id: UUID,
        turn_id: UUID,
        now: datetime | None = None,
    ) -> RealtimeAssistance:
        if self.task_id is not None or self.turn_id is not None:
            if self.task_id == task_id and self.turn_id == turn_id:
                return self
            raise ValueError("Realtime Assistance execution is already linked")
        return self._transition(
            RealtimeAssistanceStatus.RUNNING,
            task_id=task_id,
            turn_id=turn_id,
            error_code=None,
            now=now,
        )

    def await_approval(self, *, now: datetime | None = None) -> RealtimeAssistance:
        return self._transition(
            RealtimeAssistanceStatus.AWAITING_APPROVAL,
            requires_user_confirmation=True,
            now=now,
        )

    def resume(self, *, now: datetime | None = None) -> RealtimeAssistance:
        return self._transition(
            RealtimeAssistanceStatus.RUNNING,
            requires_user_confirmation=False,
            now=now,
        )

    def complete(
        self,
        *,
        message_id: UUID,
        spoken_summary: str,
        display_markdown: str,
        citations: tuple[RealtimeAssistanceCitation, ...] = (),
        freshness: str | None = None,
        now: datetime | None = None,
    ) -> RealtimeAssistance:
        if len(citations) > 32:
            raise ValueError("Realtime Assistance supports at most 32 citations")
        normalized_summary = _clean_text(spoken_summary, name="spoken_summary", maximum=320)
        if not display_markdown.strip() or len(display_markdown) > 100_000:
            raise ValueError("display_markdown must contain 1 to 100000 characters")
        return self._transition(
            RealtimeAssistanceStatus.COMPLETED,
            message_id=message_id,
            spoken_summary=normalized_summary,
            display_markdown=display_markdown,
            citations=citations,
            freshness=(
                _clean_text(freshness, name="freshness", maximum=128)
                if freshness is not None
                else None
            ),
            requires_user_confirmation=False,
            now=now,
        )

    def fail(
        self,
        error_code: str,
        *,
        now: datetime | None = None,
    ) -> RealtimeAssistance:
        return self._transition(
            RealtimeAssistanceStatus.FAILED,
            error_code=_clean_text(error_code, name="error_code", maximum=128),
            requires_user_confirmation=False,
            now=now,
        )

    def cancel(self, *, now: datetime | None = None) -> RealtimeAssistance:
        return self._transition(
            RealtimeAssistanceStatus.CANCELLED,
            requires_user_confirmation=False,
            now=now,
        )

    def _transition(
        self,
        status: RealtimeAssistanceStatus,
        *,
        now: datetime | None = None,
        **changes: object,
    ) -> RealtimeAssistance:
        if status not in _ASSISTANCE_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"cannot transition RealtimeAssistance from {self.status} to {status}"
            )
        return replace(
            self,
            status=status,
            updated_at=(now or _now()).astimezone(UTC),
            revision=self.revision + 1,
            **changes,
        )


__all__ = [
    "GameMemoryDigest",
    "RealtimeAssistance",
    "RealtimeAssistanceCitation",
    "RealtimeAssistanceStatus",
    "RealtimeCaptionSpeaker",
    "RealtimeMemoryMode",
    "RealtimeProvider",
    "RealtimeSession",
    "RealtimeSessionStatus",
    "RealtimeTranscriptEntry",
    "RealtimeVoiceMode",
]
