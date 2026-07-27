from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from fairy_core.persona import (
    AmbientSurface,
    DialogueSource,
    DialogueTrigger,
    RealtimeActivityProfile,
    RealtimeInteractionIntensity,
)


class AmbientContextSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    locale: str = Field(default="en", min_length=2, max_length=16)
    surface: AmbientSurface = AmbientSurface.HIDDEN
    user_idle_seconds: int = Field(default=0, ge=0)
    startup_eligible: bool = False
    user_returned: bool = False
    network_restored: bool = False
    battery_percent: int | None = Field(default=None, ge=0, le=100)
    charging: bool | None = None
    charging_started: bool = False
    locked: bool = False
    do_not_disturb: bool = False
    typing: bool = False
    input_open: bool = False
    microphone_active: bool = False
    fullscreen: bool = False
    realtime_active: bool = False
    active_turn: bool = False
    approval_waiting: bool = False
    severe_error: bool = False
    tts_active: bool = False


class AmbientDialoguePreferencesModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    voice_enabled: bool = False
    generated_enabled: bool = False


class AmbientDialogueStateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_date: str | None = None
    startup_date: str | None = None
    daily_text_count: int = Field(default=0, ge=0)
    daily_voice_count: int = Field(default=0, ge=0)
    daily_generated_count: int = Field(default=0, ge=0)
    last_global_at: datetime | None = None
    category_last_at: dict[str, datetime] = Field(default_factory=dict)
    line_last_at: dict[str, datetime] = Field(default_factory=dict)
    returned_last_at: datetime | None = None
    generated_digests: list[str] = Field(default_factory=list, max_length=64)


class AmbientDialogueEvaluateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: AmbientContextSnapshotModel
    preferences: AmbientDialoguePreferencesModel = Field(
        default_factory=AmbientDialoguePreferencesModel
    )
    state: AmbientDialogueStateModel = Field(default_factory=AmbientDialogueStateModel)


class GeneratedDialogueRequestModel(BaseModel):
    trigger: DialogueTrigger
    locale: str
    persona_digest: str
    safe_facts: list[str]
    recent_categories: list[str]
    recent_digests: list[str]
    max_output_tokens: int = 160


class GeneratedDialogueCandidateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=280)
    intent: str = Field(min_length=1, max_length=64)
    required_facts: list[str] = Field(default_factory=list, max_length=32)
    safe_for_tts: bool
    cooldown_group: str = Field(min_length=1, max_length=48)


class AmbientDialogueProjectionModel(BaseModel):
    presentation_id: str
    dialogue_id: str
    text: str
    source: DialogueSource
    trigger: DialogueTrigger
    locale: str
    tts_allowed: bool
    expires_at: datetime
    persona_digest: str


class AmbientDialogueDecisionModel(BaseModel):
    projection: AmbientDialogueProjectionModel | None
    generation_request: GeneratedDialogueRequestModel | None
    next_state: AmbientDialogueStateModel
    reason: str


class RealtimePersonaSnapshotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: str = Field(default="zh-CN", min_length=2, max_length=16)
    activity_profile: RealtimeActivityProfile = RealtimeActivityProfile.AUTO
    interaction_intensity: RealtimeInteractionIntensity = RealtimeInteractionIntensity.STANDARD
    current_goal: str | None = Field(default=None, min_length=1, max_length=500)
    subject_title: str | None = Field(default=None, min_length=1, max_length=160)
    recent_progress: str | None = Field(default=None, min_length=1, max_length=800)


class RealtimeIdentitySnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    role: str


class RealtimeRelationshipSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_has_final_authority: bool
    protect_privacy_time_and_work: bool


class RealtimeSpeechSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lead_with_conclusion: bool
    dry_humour: str
    use_master: str
    no_customer_service_filler: bool
    no_empty_praise: bool


class RealtimePolicySnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proactive_allowed: bool
    max_spoken_sentences: int = Field(ge=1, le=4)
    grounding_required: bool
    never_claim_unobserved_action: bool


class RealtimeShortMemorySnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_goal: str | None
    subject_title: str | None
    recent_progress: str | None


class RealtimePersonaSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    persona_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    authority_version: str
    locale: str
    activity_profile: RealtimeActivityProfile
    interaction_intensity: RealtimeInteractionIntensity
    identity: RealtimeIdentitySnapshotModel
    relationship: RealtimeRelationshipSnapshotModel
    speech: RealtimeSpeechSnapshotModel
    realtime_policy: RealtimePolicySnapshotModel
    short_memory: RealtimeShortMemorySnapshotModel


__all__ = [
    "AmbientContextSnapshotModel",
    "AmbientDialogueDecisionModel",
    "AmbientDialogueEvaluateInput",
    "AmbientDialoguePreferencesModel",
    "AmbientDialogueProjectionModel",
    "AmbientDialogueStateModel",
    "GeneratedDialogueCandidateModel",
    "GeneratedDialogueRequestModel",
    "RealtimeIdentitySnapshotModel",
    "RealtimePersonaSnapshotInput",
    "RealtimePersonaSnapshotModel",
    "RealtimePolicySnapshotModel",
    "RealtimeRelationshipSnapshotModel",
    "RealtimeShortMemorySnapshotModel",
    "RealtimeSpeechSnapshotModel",
]
