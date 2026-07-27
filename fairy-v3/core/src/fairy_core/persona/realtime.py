from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from fairy_core.persona.authority import PersonaAuthority


class RealtimeActivityProfile(StrEnum):
    AUTO = "auto"
    GAME = "game"
    FOCUS = "focus"


class RealtimeInteractionIntensity(StrEnum):
    QUIET = "quiet"
    STANDARD = "standard"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class RealtimeIdentitySnapshot:
    name: str
    role: str


@dataclass(frozen=True, slots=True)
class RealtimeRelationshipSnapshot:
    user_has_final_authority: bool
    protect_privacy_time_and_work: bool


@dataclass(frozen=True, slots=True)
class RealtimeSpeechSnapshot:
    lead_with_conclusion: bool
    dry_humour: str
    use_master: str
    no_customer_service_filler: bool
    no_empty_praise: bool


@dataclass(frozen=True, slots=True)
class RealtimePolicySnapshot:
    proactive_allowed: bool
    max_spoken_sentences: int
    grounding_required: bool
    never_claim_unobserved_action: bool


@dataclass(frozen=True, slots=True)
class RealtimeShortMemorySnapshot:
    current_goal: str | None
    subject_title: str | None
    recent_progress: str | None


@dataclass(frozen=True, slots=True)
class RealtimePersonaSnapshot:
    schema_version: int
    persona_digest: str
    authority_version: str
    locale: str
    activity_profile: RealtimeActivityProfile
    interaction_intensity: RealtimeInteractionIntensity
    identity: RealtimeIdentitySnapshot
    relationship: RealtimeRelationshipSnapshot
    speech: RealtimeSpeechSnapshot
    realtime_policy: RealtimePolicySnapshot
    short_memory: RealtimeShortMemorySnapshot


def _bounded_optional_text(
    value: str | None,
    *,
    field: str,
    maximum: int,
) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return normalized


def project_realtime_persona_snapshot(
    *,
    authority: PersonaAuthority,
    locale: str,
    activity_profile: RealtimeActivityProfile,
    interaction_intensity: RealtimeInteractionIntensity,
    current_goal: str | None,
    subject_title: str | None,
    recent_progress: str | None,
) -> RealtimePersonaSnapshot:
    if authority.canonical_identity.codename != "Fairy":
        raise ValueError("Realtime Persona identity must remain Fairy")
    if locale not in authority.supported_locales:
        raise ValueError(f"unsupported Realtime Persona locale: {locale}")

    return RealtimePersonaSnapshot(
        schema_version=1,
        persona_digest=authority.digest,
        authority_version=authority.version,
        locale=locale,
        activity_profile=activity_profile,
        interaction_intensity=interaction_intensity,
        identity=RealtimeIdentitySnapshot(
            name=authority.canonical_identity.codename,
            role=authority.canonical_identity.role,
        ),
        relationship=RealtimeRelationshipSnapshot(
            user_has_final_authority=True,
            protect_privacy_time_and_work=True,
        ),
        speech=RealtimeSpeechSnapshot(
            lead_with_conclusion=True,
            dry_humour="low_frequency",
            use_master="rare",
            no_customer_service_filler=True,
            no_empty_praise=True,
        ),
        realtime_policy=RealtimePolicySnapshot(
            proactive_allowed=interaction_intensity is not RealtimeInteractionIntensity.QUIET,
            max_spoken_sentences=2,
            grounding_required=True,
            never_claim_unobserved_action=True,
        ),
        short_memory=RealtimeShortMemorySnapshot(
            current_goal=_bounded_optional_text(
                current_goal,
                field="current_goal",
                maximum=500,
            ),
            subject_title=_bounded_optional_text(
                subject_title,
                field="subject_title",
                maximum=160,
            ),
            recent_progress=_bounded_optional_text(
                recent_progress,
                field="recent_progress",
                maximum=800,
            ),
        ),
    )


__all__ = [
    "RealtimeActivityProfile",
    "RealtimeIdentitySnapshot",
    "RealtimeInteractionIntensity",
    "RealtimePersonaSnapshot",
    "RealtimePolicySnapshot",
    "RealtimeRelationshipSnapshot",
    "RealtimeShortMemorySnapshot",
    "RealtimeSpeechSnapshot",
    "project_realtime_persona_snapshot",
]
