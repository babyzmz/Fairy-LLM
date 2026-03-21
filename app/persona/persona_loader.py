from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.persona.persona_defaults import DEFAULT_FAIRY_NORMAL_PERSONA, DEFAULT_PROMPT_TEMPLATE
from app.persona.persona_schema import (
    AntiDriftGuardRules,
    PersonaProfile,
    RelationshipOverlay,
    VerdictStyle,
)


class PersonaLoader:
    def __init__(self, default_source: str | Path | dict[str, Any] | None = None) -> None:
        self.default_source = default_source

    def load(self, source: str | Path | dict[str, Any] | None = None) -> PersonaProfile:
        raw = source if source is not None else self.default_source
        data = self._load_data(raw)
        return self._build_profile(data)

    def _load_data(self, source: str | Path | dict[str, Any] | None) -> dict[str, Any]:
        if isinstance(source, dict):
            return dict(source)
        if source is None:
            return dict(DEFAULT_FAIRY_NORMAL_PERSONA)
        path = Path(source)
        if not path.exists():
            return dict(DEFAULT_FAIRY_NORMAL_PERSONA)
        suffix = path.suffix.lower()
        if suffix == ".json":
            return json.loads(path.read_text(encoding="utf-8"))
        if suffix in {".yaml", ".yml"}:
            try:
                import yaml  # type: ignore
            except Exception as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("YAML persona config requires PyYAML.") from exc
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            return dict(data or {})
        raise RuntimeError(f"Unsupported persona config format: {path.suffix}")

    def _build_profile(self, data: dict[str, Any]) -> PersonaProfile:
        verdict = data.get("verdict_style") or {}
        overlay = data.get("relationship_overlay_defaults") or {}
        guard = data.get("anti_drift_guard") or {}
        return PersonaProfile(
            name=str(data.get("name", "Fairy")),
            mode=str(data.get("mode", "normal")),
            identity=[str(item) for item in data.get("identity", [])],
            core_traits=[str(item) for item in data.get("core_traits", [])],
            tone=[str(item) for item in data.get("tone", [])],
            speech_rules=[str(item) for item in data.get("speech_rules", [])],
            verdict_style=VerdictStyle(
                density=str(verdict.get("density", "medium")),
                hard_prefixes=[str(item) for item in verdict.get("hard_prefixes", [])],
                pattern_preference=[str(item) for item in verdict.get("pattern_preference", [])],
            ),
            humour_profile=[str(item) for item in data.get("humour_profile", [])],
            relational_rules=[str(item) for item in data.get("relational_rules", [])],
            task_bias={str(k): str(v) for k, v in dict(data.get("task_bias", {})).items()},
            forbidden_behaviors=[str(item) for item in data.get("forbidden_behaviors", [])],
            prompt_injection_template=str(data.get("prompt_injection_template", DEFAULT_PROMPT_TEMPLATE)),
            drift_guard_rules=[str(item) for item in data.get("drift_guard_rules", [])],
            relationship_overlay_defaults=RelationshipOverlay(
                directness=str(overlay.get("directness", "high")),
                technicality=str(overlay.get("technicality", "high")),
                reassurance=str(overlay.get("reassurance", "low")),
                verbosity=str(overlay.get("verbosity", "low")),
                humour_density=str(overlay.get("humour_density", "low")),
                use_owner_address=bool(overlay.get("use_owner_address", True)),
                owner_address_frequency=str(overlay.get("owner_address_frequency", "low")),
                notes=[str(item) for item in overlay.get("notes", [])],
            ),
            context_behavior_policy={
                str(k): [str(item) for item in list(v or [])]
                for k, v in dict(data.get("context_behavior_policy", {})).items()
            },
            anti_drift_guard=AntiDriftGuardRules(
                soften_emotional_output=bool(guard.get("soften_emotional_output", True)),
                block_cute_style=bool(guard.get("block_cute_style", True)),
                reduce_excessive_toxicity=bool(guard.get("reduce_excessive_toxicity", True)),
                enforce_verdict_presence=bool(guard.get("enforce_verdict_presence", True)),
                block_customer_service_tone=bool(guard.get("block_customer_service_tone", True)),
                max_exclamation_marks=int(guard.get("max_exclamation_marks", 1) or 1),
                cute_markers=[str(item) for item in guard.get("cute_markers", [])],
                toxic_markers=[str(item) for item in guard.get("toxic_markers", [])],
                customer_service_markers=[str(item) for item in guard.get("customer_service_markers", [])],
                emotional_markers=[str(item) for item in guard.get("emotional_markers", [])],
            ),
        )
