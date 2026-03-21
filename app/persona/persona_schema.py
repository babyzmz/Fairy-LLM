from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class VerdictStyle:
    density: str = "medium"
    hard_prefixes: list[str] = field(default_factory=list)
    pattern_preference: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RelationshipOverlay:
    directness: str = "high"
    technicality: str = "high"
    reassurance: str = "low"
    verbosity: str = "low"
    humour_density: str = "low"
    use_owner_address: bool = True
    owner_address_frequency: str = "low"
    notes: list[str] = field(default_factory=list)

    def clone(self) -> "RelationshipOverlay":
        return RelationshipOverlay(
            directness=self.directness,
            technicality=self.technicality,
            reassurance=self.reassurance,
            verbosity=self.verbosity,
            humour_density=self.humour_density,
            use_owner_address=self.use_owner_address,
            owner_address_frequency=self.owner_address_frequency,
            notes=list(self.notes),
        )


@dataclass(slots=True)
class AntiDriftGuardRules:
    soften_emotional_output: bool = True
    block_cute_style: bool = True
    reduce_excessive_toxicity: bool = True
    enforce_verdict_presence: bool = True
    block_customer_service_tone: bool = True
    max_exclamation_marks: int = 1
    cute_markers: list[str] = field(default_factory=list)
    toxic_markers: list[str] = field(default_factory=list)
    customer_service_markers: list[str] = field(default_factory=list)
    emotional_markers: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PersonaProfile:
    name: str
    mode: str
    identity: list[str]
    core_traits: list[str]
    tone: list[str]
    speech_rules: list[str]
    verdict_style: VerdictStyle
    humour_profile: list[str]
    relational_rules: list[str]
    task_bias: dict[str, str]
    forbidden_behaviors: list[str]
    prompt_injection_template: str
    drift_guard_rules: list[str]
    relationship_overlay_defaults: RelationshipOverlay = field(default_factory=RelationshipOverlay)
    context_behavior_policy: dict[str, list[str]] = field(default_factory=dict)
    anti_drift_guard: AntiDriftGuardRules = field(default_factory=AntiDriftGuardRules)


@dataclass(slots=True)
class PersonaRenderContext:
    task_type: str
    user_profile: list[dict[str, Any]] = field(default_factory=list)
    recent_summary: str = ""
    chosen_skill: str = ""
    memory_category: str = ""
    extra_context: dict[str, Any] = field(default_factory=dict)
