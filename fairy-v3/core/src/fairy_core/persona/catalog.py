from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any

import yaml

from fairy_core.persona.authority import load_default_persona_authority
from fairy_core.persona.resources import persona_resource_root

_KNOWN_FACTS = frozenset(
    {
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
        "battery_low",
        "charging",
    }
)
_UNSUPPORTED_CLAIMS = (
    "摄像头",
    "邮件正文",
    "相册",
    "剪贴板",
    "屏幕内容",
    "联系警方",
    "camera feed",
    "email body",
    "photo library",
    "clipboard",
    "screen contents",
    "contacted the police",
)


class DialogueSource(StrEnum):
    PROTECTED = "protected"
    AUTHORED_ORIGINAL = "authored_original"
    GENERATED_ORIGINAL = "generated_original"


class DialogueTrigger(StrEnum):
    STARTUP = "startup"
    IDLE_SHORT = "idle_short"
    IDLE_LONG = "idle_long"
    USER_RETURNED = "user_returned"
    NETWORK_RESTORED = "network_restored"
    BATTERY_LOW = "battery_low"
    CHARGING_STARTED = "charging_started"
    SELF_COMMENTARY = "self_commentary"


@dataclass(frozen=True, slots=True)
class DialogueCatalogEntry:
    semantic_id: str
    locale: str
    source: DialogueSource
    text: str
    trigger: DialogueTrigger
    context_tags: tuple[str, ...]
    risk_tags: tuple[str, ...]
    tts_allowed: bool
    cooldown_group: str
    required_facts: tuple[str, ...]
    persona_version: str

    @property
    def sentence_count(self) -> int:
        parts = [
            part
            for part in re.split(r"[.!?\u3002\uff01\uff1f]+", self.text.strip())
            if part.strip()
        ]
        return max(1, len(parts))


class DialogueCatalog:
    def __init__(self, items: tuple[DialogueCatalogEntry, ...]) -> None:
        if not items:
            raise ValueError("Dialogue Catalog cannot be empty")
        self.items = items
        self.locales = tuple(sorted({item.locale for item in items}))
        self._by_key = {(item.semantic_id, item.locale): item for item in items}
        if len(self._by_key) != len(items):
            raise ValueError("Dialogue Catalog contains duplicate locale entries")
        semantic_locales: dict[str, set[str]] = {}
        for item in items:
            semantic_locales.setdefault(item.semantic_id, set()).add(item.locale)
            if item.source not in {
                DialogueSource.PROTECTED,
                DialogueSource.AUTHORED_ORIGINAL,
            }:
                raise ValueError("Only reviewed original dialogue can enter the runtime catalog")
            if item.sentence_count > 3 or len(item.text) > 280:
                raise ValueError(f"Dialogue line {item.semantic_id!r} exceeds its text bound")
            if item.risk_tags:
                raise ValueError(f"Reviewed runtime line {item.semantic_id!r} contains risk tags")
            unknown_facts = set(item.required_facts) - _KNOWN_FACTS
            if unknown_facts:
                raise ValueError(
                    f"Dialogue line {item.semantic_id!r} requires unknown device facts"
                )
            placeholders = set(re.findall(r"\{([a-z][a-z0-9_]*)\}", item.text))
            if not placeholders.issubset(item.required_facts):
                raise ValueError(f"Dialogue line {item.semantic_id!r} has an unbound placeholder")
            lowered = item.text.casefold()
            if any(marker.casefold() in lowered for marker in _UNSUPPORTED_CLAIMS):
                raise ValueError(
                    f"Dialogue line {item.semantic_id!r} claims unsupported device access"
                )
        required_locales = set(self.locales)
        if any(locales != required_locales for locales in semantic_locales.values()):
            raise ValueError("Dialogue Catalog locales do not have semantic parity")

    def for_locale(self, locale: str) -> tuple[DialogueCatalogEntry, ...]:
        selected = tuple(item for item in self.items if item.locale == locale)
        if selected:
            return selected
        return tuple(item for item in self.items if item.locale == "en")

    def get(self, semantic_id: str, locale: str) -> DialogueCatalogEntry:
        item = self._by_key.get((semantic_id, locale)) or self._by_key.get((semantic_id, "en"))
        if item is None:
            raise KeyError(f"Dialogue line is unavailable: {semantic_id}/{locale}")
        return item


def _text_tuple(value: Any, *, field: str, semantic_id: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Dialogue {semantic_id!r} field {field!r} must be a text list")
    return tuple(item.strip() for item in value if item.strip())


@lru_cache(maxsize=1)
def load_default_dialogue_catalog() -> DialogueCatalog:
    authority = load_default_persona_authority()
    path = persona_resource_root() / "dialogue-catalog.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Dialogue Catalog schema_version must be 1")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("Dialogue Catalog items must be a list")
    items: list[DialogueCatalogEntry] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Dialogue Catalog entries must be mappings")
        semantic_id = str(raw.get("id", "")).strip()
        texts = raw.get("texts")
        if not semantic_id or not isinstance(texts, dict):
            raise ValueError("Dialogue Catalog entry requires id and localized texts")
        source = DialogueSource(str(raw.get("source", "")))
        trigger = DialogueTrigger(str(raw.get("trigger", "")))
        persona_version = str(raw.get("persona_version", "")).strip()
        if persona_version != authority.version:
            raise ValueError(f"Dialogue {semantic_id!r} has a stale Persona version")
        locales = set(texts)
        if locales != set(authority.supported_locales):
            raise ValueError(f"Dialogue {semantic_id!r} does not cover every supported locale")
        for locale, text in texts.items():
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"Dialogue {semantic_id!r} locale {locale!r} is empty")
            items.append(
                DialogueCatalogEntry(
                    semantic_id=semantic_id,
                    locale=locale,
                    source=source,
                    text=text.strip(),
                    trigger=trigger,
                    context_tags=_text_tuple(
                        raw.get("context_tags", []),
                        field="context_tags",
                        semantic_id=semantic_id,
                    ),
                    risk_tags=_text_tuple(
                        raw.get("risk_tags", []), field="risk_tags", semantic_id=semantic_id
                    ),
                    tts_allowed=bool(raw.get("tts_allowed", False)),
                    cooldown_group=str(raw.get("cooldown_group", trigger.value)).strip(),
                    required_facts=_text_tuple(
                        raw.get("required_facts", []),
                        field="required_facts",
                        semantic_id=semantic_id,
                    ),
                    persona_version=persona_version,
                )
            )
    catalog = DialogueCatalog(tuple(items))
    protected_ids = {
        item.semantic_id for item in catalog.items if item.source is DialogueSource.PROTECTED
    }
    if protected_ids != set(authority.protected_utterance_ids):
        raise ValueError("Protected dialogue does not match Persona Authority")
    return catalog


__all__ = [
    "DialogueCatalog",
    "DialogueCatalogEntry",
    "DialogueSource",
    "DialogueTrigger",
    "load_default_dialogue_catalog",
]
