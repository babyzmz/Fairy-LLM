from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml

from fairy_core.persona.resources import persona_resource_root


@dataclass(frozen=True, slots=True)
class CanonicalIdentity:
    designation: str
    codename: str
    role: str


@dataclass(frozen=True, slots=True)
class RelationshipContract:
    user_authority: str
    loyalty: str
    correction_boundary: str


@dataclass(frozen=True, slots=True)
class ProhibitedDrift:
    rules: tuple[str, ...]
    allow_persona_selector: bool = False


@dataclass(frozen=True, slots=True)
class PersonaAuthority:
    version: str
    canonical_identity: CanonicalIdentity
    relationship_contract: RelationshipContract
    speech_constitution: tuple[str, ...]
    scenario_policy: dict[str, tuple[str, ...]]
    protected_utterance_ids: tuple[str, ...]
    prohibited_drift: ProhibitedDrift
    supported_locales: tuple[str, ...]
    system_prompt: str
    digest: str


def _canonical_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _required_mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Persona Authority field {key!r} must be a mapping")
    return value


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Persona Authority field {key!r} must be text")
    return value.strip()


def _text_tuple(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise ValueError(f"Persona Authority field {key!r} must be a non-empty text list")
    return tuple(item.strip() for item in value)


@lru_cache(maxsize=1)
def load_default_persona_authority() -> PersonaAuthority:
    path = persona_resource_root() / "persona-authority.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Persona Authority resource must be a mapping")
    identity = _required_mapping(payload, "canonical_identity")
    relationship = _required_mapping(payload, "relationship_contract")
    drift = _required_mapping(payload, "prohibited_drift")
    scenario = _required_mapping(payload, "scenario_policy")
    scenario_policy = {
        name: tuple(str(item).strip() for item in rules)
        for name, rules in scenario.items()
        if isinstance(name, str)
        and isinstance(rules, list)
        and rules
        and all(isinstance(item, str) and item.strip() for item in rules)
    }
    if set(scenario_policy) != set(scenario):
        raise ValueError("Persona scenario policies must be non-empty text lists")
    allow_selector = drift.get("allow_persona_selector", False)
    if not isinstance(allow_selector, bool):
        raise ValueError("allow_persona_selector must be boolean")
    authority = PersonaAuthority(
        version=_required_text(payload, "version"),
        canonical_identity=CanonicalIdentity(
            designation=_required_text(identity, "designation"),
            codename=_required_text(identity, "codename"),
            role=_required_text(identity, "role"),
        ),
        relationship_contract=RelationshipContract(
            user_authority=_required_text(relationship, "user_authority"),
            loyalty=_required_text(relationship, "loyalty"),
            correction_boundary=_required_text(relationship, "correction_boundary"),
        ),
        speech_constitution=_text_tuple(payload, "speech_constitution"),
        scenario_policy=scenario_policy,
        protected_utterance_ids=_text_tuple(payload, "protected_utterance_ids"),
        prohibited_drift=ProhibitedDrift(
            rules=_text_tuple(drift, "rules"),
            allow_persona_selector=allow_selector,
        ),
        supported_locales=tuple(sorted(_text_tuple(payload, "supported_locales"))),
        system_prompt=_required_text(payload, "system_prompt"),
        digest=hashlib.sha256(_canonical_payload(payload)).hexdigest(),
    )
    if authority.canonical_identity.codename != "Fairy":
        raise ValueError("The canonical Persona codename must remain Fairy")
    if authority.prohibited_drift.allow_persona_selector:
        raise ValueError("Fairy V3 does not support alternate Persona selection")
    return authority


__all__ = [
    "CanonicalIdentity",
    "PersonaAuthority",
    "ProhibitedDrift",
    "RelationshipContract",
    "load_default_persona_authority",
]
