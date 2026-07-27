from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field, model_validator

from fairy_core.contracts.common import ContractModel
from fairy_core.realtime.models import (
    RealtimeAssistanceStatus,
    RealtimeCaptionSpeaker,
    RealtimeMemoryMode,
    RealtimeProvider,
    RealtimeSessionStatus,
    RealtimeVoiceMode,
)

BoundedObservedFact = Annotated[str, Field(min_length=1, max_length=300)]


class RealtimeProviderSelection(StrEnum):
    AUTO = "auto"
    LOCAL_MINI_CPM_O45 = RealtimeProvider.LOCAL_MINI_CPM_O45.value
    GEMINI_LIVE = RealtimeProvider.GEMINI_LIVE.value
    GLM_REALTIME_FLASH = RealtimeProvider.GLM_REALTIME_FLASH.value
    GLM_REALTIME_AIR = RealtimeProvider.GLM_REALTIME_AIR.value


class RealtimeSessionStartInput(ContractModel):
    device_id: str = Field(min_length=1, max_length=128)
    conversation_id: UUID | None = None
    provider: RealtimeProviderSelection = RealtimeProviderSelection.AUTO
    locale: str = Field(default="zh-CN", min_length=2, max_length=32)
    voice_mode: RealtimeVoiceMode = RealtimeVoiceMode.NATIVE
    memory_mode: RealtimeMemoryMode = RealtimeMemoryMode.PROGRESS_DIGEST
    microphone_consent: bool
    screen_consent: bool = False
    game_audio_consent: bool = False
    idempotency_key: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_consent(self) -> RealtimeSessionStartInput:
        if not self.microphone_consent:
            raise ValueError("microphone consent is required")
        if self.game_audio_consent and not self.screen_consent:
            raise ValueError("game audio requires consent for the selected game window")
        return self


class RealtimeSessionIdInput(ContractModel):
    session_id: UUID


class RealtimeSessionListInput(ContractModel):
    limit: int = Field(default=50, ge=1, le=200)


class RealtimeSessionReportInput(ContractModel):
    session_id: UUID
    status: RealtimeSessionStatus
    expected_revision: int = Field(ge=1)
    audio_input_ms: int = Field(default=0, ge=0)
    audio_output_ms: int = Field(default=0, ge=0)
    video_frame_count: int = Field(default=0, ge=0)
    interruption_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    error_code: str | None = Field(default=None, min_length=1, max_length=128)


class RealtimeSessionStopInput(ContractModel):
    session_id: UUID
    expected_revision: int = Field(ge=1)


class RealtimeSessionModel(ContractModel):
    id: UUID
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


class RealtimeSessionPageModel(ContractModel):
    items: tuple[RealtimeSessionModel, ...]


class RealtimeAssistanceRequestInput(ContractModel):
    session_id: UUID
    conversation_id: UUID
    request_id: str = Field(min_length=1, max_length=128)
    segment_id: str = Field(min_length=1, max_length=128)
    context_epoch: int = Field(ge=1)
    question: str = Field(min_length=1, max_length=4_000)
    activity_profile: str = Field(min_length=1, max_length=32)
    application_title: str | None = Field(default=None, min_length=1, max_length=128)
    observed_facts: tuple[BoundedObservedFact, ...] = Field(
        default_factory=tuple,
        max_length=16,
    )
    allow_network: bool = False
    locale: str = Field(default="zh-CN", min_length=2, max_length=32)


class RealtimeAssistanceGetInput(ContractModel):
    session_id: UUID
    request_id: str = Field(min_length=1, max_length=128)


class RealtimeAssistanceCancelInput(RealtimeAssistanceGetInput):
    expected_revision: int = Field(ge=1)


class RealtimeAssistanceCitationModel(ContractModel):
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=4_096)


class RealtimeAssistanceModel(ContractModel):
    id: UUID
    session_id: UUID
    conversation_id: UUID
    request_id: str
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
    citations: tuple[RealtimeAssistanceCitationModel, ...]
    freshness: str | None
    requires_user_confirmation: bool
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    revision: int


class GameMemorySaveInput(ContractModel):
    session_id: UUID
    game_title: str = Field(min_length=1, max_length=160)
    played_at: datetime
    duration_seconds: int = Field(ge=0, le=86_400)
    activities: tuple[str, ...] = Field(max_length=5)
    progress_summary: str = Field(min_length=1, max_length=800)
    next_goal: str | None = Field(default=None, min_length=1, max_length=300)
    notable_outcome: str | None = Field(default=None, min_length=1, max_length=300)


class GameMemoryIdInput(ContractModel):
    memory_id: UUID


class GameMemoryListInput(ContractModel):
    limit: int = Field(default=50, ge=1, le=200)


class GameMemoryDigestModel(ContractModel):
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


class GameMemoryPageModel(ContractModel):
    items: tuple[GameMemoryDigestModel, ...]


class GameMemoryDeleteResult(ContractModel):
    memory_id: UUID
    deleted: bool


class RealtimeTranscriptAppendInput(ContractModel):
    session_id: UUID
    speaker: RealtimeCaptionSpeaker
    text: str = Field(min_length=1, max_length=4_000)


class RealtimeTranscriptListInput(ContractModel):
    conversation_id: UUID
    limit: int = Field(default=500, ge=1, le=2_000)


class RealtimeTranscriptEntryModel(ContractModel):
    id: UUID
    session_id: UUID
    conversation_id: UUID
    sequence: int
    speaker: RealtimeCaptionSpeaker
    text: str
    created_at: datetime


class RealtimeTranscriptPageModel(ContractModel):
    items: tuple[RealtimeTranscriptEntryModel, ...]


__all__ = [
    "GameMemoryDeleteResult",
    "GameMemoryDigestModel",
    "GameMemoryIdInput",
    "GameMemoryListInput",
    "GameMemoryPageModel",
    "GameMemorySaveInput",
    "RealtimeAssistanceCancelInput",
    "RealtimeAssistanceCitationModel",
    "RealtimeAssistanceGetInput",
    "RealtimeAssistanceModel",
    "RealtimeAssistanceRequestInput",
    "RealtimeCaptionSpeaker",
    "RealtimeProviderSelection",
    "RealtimeSessionIdInput",
    "RealtimeSessionListInput",
    "RealtimeSessionModel",
    "RealtimeSessionPageModel",
    "RealtimeSessionReportInput",
    "RealtimeSessionStartInput",
    "RealtimeSessionStopInput",
    "RealtimeTranscriptAppendInput",
    "RealtimeTranscriptEntryModel",
    "RealtimeTranscriptListInput",
    "RealtimeTranscriptPageModel",
]
