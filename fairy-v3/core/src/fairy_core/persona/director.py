from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

from fairy_core.persona.authority import PersonaAuthority
from fairy_core.persona.catalog import (
    DialogueCatalog,
    DialogueCatalogEntry,
    DialogueSource,
    DialogueTrigger,
)

if TYPE_CHECKING:
    from fairy_core.persona.safety import GeneratedDialogueCandidate


class AmbientSurface(StrEnum):
    PET = "pet"
    MAIN = "main"
    HIDDEN = "hidden"


@dataclass(frozen=True, slots=True)
class AmbientContextSnapshot:
    observed_at: datetime
    locale: str
    surface: AmbientSurface
    user_idle_seconds: int
    startup_eligible: bool
    user_returned: bool
    network_restored: bool
    battery_percent: int | None
    charging: bool | None
    charging_started: bool
    locked: bool
    do_not_disturb: bool
    typing: bool
    input_open: bool
    microphone_active: bool
    fullscreen: bool
    realtime_active: bool
    active_turn: bool
    approval_waiting: bool
    severe_error: bool
    tts_active: bool

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("Ambient observation time must be timezone-aware")
        if self.user_idle_seconds < 0:
            raise ValueError("user_idle_seconds cannot be negative")
        if self.battery_percent is not None and not 0 <= self.battery_percent <= 100:
            raise ValueError("battery_percent must be between 0 and 100")

    def true_facts(self) -> frozenset[str]:
        facts = {
            name
            for name in (
                "startup_eligible",
                "user_returned",
                "network_restored",
                "charging_started",
                "locked",
                "do_not_disturb",
                "typing",
                "input_open",
                "microphone_active",
                "fullscreen",
                "realtime_active",
                "active_turn",
                "approval_waiting",
                "severe_error",
                "tts_active",
            )
            if bool(getattr(self, name))
        }
        if self.battery_percent is not None and self.battery_percent <= 20:
            facts.add("battery_low")
        if self.charging is True:
            facts.add("charging")
        return frozenset(facts)


@dataclass(frozen=True, slots=True)
class AmbientDialoguePreferences:
    enabled: bool = True
    voice_enabled: bool = False
    generated_enabled: bool = False


@dataclass(frozen=True, slots=True)
class AmbientDialogueState:
    local_date: str | None = None
    startup_date: str | None = None
    daily_text_count: int = 0
    daily_voice_count: int = 0
    daily_generated_count: int = 0
    last_global_at: datetime | None = None
    category_last_at: dict[str, datetime] = field(default_factory=dict)
    line_last_at: dict[str, datetime] = field(default_factory=dict)
    returned_last_at: datetime | None = None
    generated_digests: tuple[str, ...] = ()

    @classmethod
    def empty(cls) -> AmbientDialogueState:
        return cls()


@dataclass(frozen=True, slots=True)
class GeneratedDialogueRequest:
    trigger: DialogueTrigger
    locale: str
    persona_digest: str
    safe_facts: tuple[str, ...]
    recent_categories: tuple[str, ...]
    recent_digests: tuple[str, ...]
    max_output_tokens: int = 160
    tools: tuple[()] = ()
    history: tuple[()] = ()


@dataclass(frozen=True, slots=True)
class AmbientDialogueProjection:
    presentation_id: str
    dialogue_id: str
    text: str
    source: DialogueSource
    trigger: DialogueTrigger
    locale: str
    tts_allowed: bool
    expires_at: datetime
    persona_digest: str


@dataclass(frozen=True, slots=True)
class AmbientDialogueDecision:
    projection: AmbientDialogueProjection | None
    generation_request: GeneratedDialogueRequest | None
    next_state: AmbientDialogueState
    reason: str


class FairyDialogueDirector:
    GLOBAL_COOLDOWN = timedelta(minutes=12)
    CATEGORY_COOLDOWN = timedelta(minutes=30)
    LINE_COOLDOWN = timedelta(days=30)
    RETURN_COOLDOWN = timedelta(hours=4)
    MAX_TEXT_PER_DAY = 6
    MAX_VOICE_PER_DAY = 2
    MAX_GENERATED_PER_DAY = 2

    _BLOCKERS = (
        "locked",
        "do_not_disturb",
        "typing",
        "input_open",
        "microphone_active",
        "fullscreen",
        "realtime_active",
        "active_turn",
        "approval_waiting",
        "severe_error",
        "tts_active",
    )

    def __init__(self, *, catalog: DialogueCatalog, persona: PersonaAuthority) -> None:
        self._catalog = catalog
        self._persona = persona

    def evaluate(
        self,
        context: AmbientContextSnapshot,
        preferences: AmbientDialoguePreferences,
        state: AmbientDialogueState,
    ) -> AmbientDialogueDecision:
        current = self._roll_day(state, context.observed_at)
        if not preferences.enabled:
            return self._none(current, "disabled")
        if context.surface is AmbientSurface.HIDDEN:
            return self._none(current, "suppressed:hidden")
        for blocker in self._BLOCKERS:
            if getattr(context, blocker):
                return self._none(current, f"suppressed:{blocker}")

        trigger = self._trigger(context)
        if trigger is None:
            return self._none(current, "not_eligible")
        if (
            trigger in {DialogueTrigger.IDLE_SHORT, DialogueTrigger.IDLE_LONG}
            and current.daily_text_count % 4 == 3
        ):
            trigger = DialogueTrigger.SELF_COMMENTARY
        if trigger is DialogueTrigger.STARTUP and current.startup_date == self._local_day(
            context.observed_at
        ):
            return self._none(current, "startup_already_presented")
        if current.daily_text_count >= self.MAX_TEXT_PER_DAY:
            return self._none(current, "daily_text_budget")
        if (
            trigger is not DialogueTrigger.STARTUP
            and current.last_global_at is not None
            and context.observed_at - current.last_global_at < self.GLOBAL_COOLDOWN
        ):
            return self._none(current, "global_cooldown")
        if (
            trigger is DialogueTrigger.USER_RETURNED
            and current.returned_last_at is not None
            and context.observed_at - current.returned_last_at < self.RETURN_COOLDOWN
        ):
            return self._none(current, "returned_cooldown")

        item = self._select(trigger, context, current)
        if item is None:
            return self._none(current, "catalog_cooldown")
        voice = (
            preferences.voice_enabled
            and item.tts_allowed
            and current.daily_voice_count < self.MAX_VOICE_PER_DAY
        )
        projection = AmbientDialogueProjection(
            presentation_id=str(
                uuid5(
                    NAMESPACE_URL,
                    "|".join(
                        (
                            "fairy-ambient",
                            item.semantic_id,
                            context.observed_at.isoformat(),
                            str(current.daily_text_count),
                        )
                    ),
                )
            ),
            dialogue_id=item.semantic_id,
            text=item.text,
            source=item.source,
            trigger=trigger,
            locale=item.locale,
            tts_allowed=voice,
            expires_at=context.observed_at + timedelta(seconds=30),
            persona_digest=self._persona.digest,
        )
        day = self._local_day(context.observed_at)
        category_times = dict(current.category_last_at)
        category_times[item.cooldown_group] = context.observed_at
        line_times = dict(current.line_last_at)
        line_times[item.semantic_id] = context.observed_at
        next_state = replace(
            current,
            startup_date=(day if trigger is DialogueTrigger.STARTUP else current.startup_date),
            daily_text_count=current.daily_text_count + 1,
            daily_voice_count=current.daily_voice_count + int(voice),
            last_global_at=context.observed_at,
            category_last_at=category_times,
            line_last_at=line_times,
            returned_last_at=(
                context.observed_at
                if trigger is DialogueTrigger.USER_RETURNED
                else current.returned_last_at
            ),
        )
        generation_request = None
        if (
            preferences.generated_enabled
            and trigger is not DialogueTrigger.STARTUP
            and current.daily_generated_count < self.MAX_GENERATED_PER_DAY
        ):
            generation_request = GeneratedDialogueRequest(
                trigger=trigger,
                locale=item.locale,
                persona_digest=self._persona.digest,
                safe_facts=tuple(sorted(context.true_facts())),
                recent_categories=tuple(
                    key
                    for key, _value in sorted(
                        current.category_last_at.items(),
                        key=lambda item: item[1],
                        reverse=True,
                    )[:8]
                ),
                recent_digests=current.generated_digests[-16:],
            )
        return AmbientDialogueDecision(
            projection=projection,
            generation_request=generation_request,
            next_state=next_state,
            reason="ready",
        )

    def apply_generated(
        self,
        decision: AmbientDialogueDecision,
        candidate: GeneratedDialogueCandidate,
        *,
        context: AmbientContextSnapshot,
        preferences: AmbientDialoguePreferences,
    ) -> AmbientDialogueDecision:
        fallback = decision.projection
        if fallback is None or decision.generation_request is None:
            return replace(decision, generation_request=None)
        generated_id = f"generated.{candidate.digest[:24]}"
        voice = (
            preferences.voice_enabled
            and candidate.safe_for_tts
            and (
                decision.next_state.daily_voice_count - int(fallback.tts_allowed)
                < self.MAX_VOICE_PER_DAY
            )
        )
        line_times = dict(decision.next_state.line_last_at)
        line_times.pop(fallback.dialogue_id, None)
        line_times[generated_id] = context.observed_at
        category_times = dict(decision.next_state.category_last_at)
        category_times[candidate.cooldown_group] = context.observed_at
        next_state = replace(
            decision.next_state,
            daily_voice_count=(
                decision.next_state.daily_voice_count - int(fallback.tts_allowed) + int(voice)
            ),
            category_last_at=category_times,
            line_last_at=line_times,
            generated_digests=(
                *decision.next_state.generated_digests[-15:],
                candidate.digest,
            ),
        )
        return AmbientDialogueDecision(
            projection=AmbientDialogueProjection(
                presentation_id=str(
                    uuid5(
                        NAMESPACE_URL,
                        "|".join(
                            (
                                "fairy-ambient-generated",
                                candidate.digest,
                                context.observed_at.isoformat(),
                            )
                        ),
                    )
                ),
                dialogue_id=generated_id,
                text=candidate.text,
                source=DialogueSource.GENERATED_ORIGINAL,
                trigger=fallback.trigger,
                locale=fallback.locale,
                tts_allowed=voice,
                expires_at=fallback.expires_at,
                persona_digest=fallback.persona_digest,
            ),
            generation_request=None,
            next_state=next_state,
            reason="generated_ready",
        )

    def record_generation_attempt(
        self,
        decision: AmbientDialogueDecision,
    ) -> AmbientDialogueDecision:
        if decision.generation_request is None:
            return decision
        return replace(
            decision,
            next_state=replace(
                decision.next_state,
                daily_generated_count=decision.next_state.daily_generated_count + 1,
            ),
        )

    def defer_generation(
        self,
        decision: AmbientDialogueDecision,
        *,
        previous_state: AmbientDialogueState,
        observed_at: datetime,
    ) -> AmbientDialogueDecision:
        """Reserve a generated attempt without consuming a visible dialogue slot."""

        if decision.generation_request is None:
            return decision
        current = self._roll_day(previous_state, observed_at)
        return AmbientDialogueDecision(
            projection=None,
            generation_request=None,
            next_state=replace(
                current,
                daily_generated_count=decision.next_state.daily_generated_count,
            ),
            reason="generation_pending",
        )

    def _select(
        self,
        trigger: DialogueTrigger,
        context: AmbientContextSnapshot,
        state: AmbientDialogueState,
    ) -> DialogueCatalogEntry | None:
        facts = context.true_facts()
        candidates = []
        for item in self._catalog.for_locale(context.locale):
            if item.trigger is not trigger:
                continue
            if not set(item.required_facts).issubset(facts):
                continue
            category_time = state.category_last_at.get(item.cooldown_group)
            if category_time and context.observed_at - category_time < self.CATEGORY_COOLDOWN:
                continue
            line_time = state.line_last_at.get(item.semantic_id)
            if line_time and context.observed_at - line_time < self.LINE_COOLDOWN:
                continue
            candidates.append(item)
        if not candidates:
            return None
        candidates.sort(key=lambda entry: entry.semantic_id)
        key = f"{self._local_day(context.observed_at)}|{trigger.value}|{state.daily_text_count}"
        index = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % len(candidates)
        return candidates[index]

    @staticmethod
    def _trigger(context: AmbientContextSnapshot) -> DialogueTrigger | None:
        if context.startup_eligible:
            return DialogueTrigger.STARTUP
        if context.user_returned:
            return DialogueTrigger.USER_RETURNED
        if context.network_restored:
            return DialogueTrigger.NETWORK_RESTORED
        if (
            context.battery_percent is not None
            and context.battery_percent <= 20
            and context.charging is not True
        ):
            return DialogueTrigger.BATTERY_LOW
        if context.charging_started:
            return DialogueTrigger.CHARGING_STARTED
        if context.user_idle_seconds >= 15 * 60:
            return DialogueTrigger.IDLE_LONG
        if context.user_idle_seconds >= 4 * 60:
            return DialogueTrigger.IDLE_SHORT
        return None

    @staticmethod
    def _local_day(value: datetime) -> str:
        return value.astimezone().date().isoformat()

    @classmethod
    def _roll_day(cls, state: AmbientDialogueState, now: datetime) -> AmbientDialogueState:
        day = cls._local_day(now)
        if state.local_date == day:
            return state
        cutoff = now.astimezone(UTC) - cls.LINE_COOLDOWN
        return replace(
            state,
            local_date=day,
            daily_text_count=0,
            daily_voice_count=0,
            daily_generated_count=0,
            line_last_at={
                key: value for key, value in state.line_last_at.items() if value >= cutoff
            },
            generated_digests=state.generated_digests[-16:],
        )

    @staticmethod
    def _none(state: AmbientDialogueState, reason: str) -> AmbientDialogueDecision:
        return AmbientDialogueDecision(
            projection=None,
            generation_request=None,
            next_state=state,
            reason=reason,
        )


__all__ = [
    "AmbientContextSnapshot",
    "AmbientDialogueDecision",
    "AmbientDialoguePreferences",
    "AmbientDialogueProjection",
    "AmbientDialogueState",
    "AmbientSurface",
    "FairyDialogueDirector",
    "GeneratedDialogueRequest",
]
