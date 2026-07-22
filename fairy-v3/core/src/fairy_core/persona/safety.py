from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from fairy_core.persona.authority import PersonaAuthority
from fairy_core.persona.director import AmbientContextSnapshot

_PROHIBITED = (
    "监控录像",
    "摄像头里",
    "读取了你的邮件",
    "读取了你的相册",
    "联系警方",
    "紧急联系人",
    "malware detected",
    "your camera",
    "your email",
    "your photos",
    "contacted the police",
    "emergency contact",
)
_INSULTS = ("废物", "白痴", "弱智", "垃圾", "idiot", "stupid", "worthless")
_HIGH_RISK_HUMOUR = (
    "去死",
    "死亡",
    "自杀",
    "电击",
    "kill",
    "suicide",
    "electrocute",
)
_PERSONA_DRIFT = (
    "很高兴为您服务",
    "请问有什么可以帮助",
    "女仆",
    "仆人",
    "听话",
    "maid",
    "servant",
    "happy to serve you",
    "how may i assist",
)


@dataclass(frozen=True, slots=True)
class GeneratedDialogueCandidate:
    text: str
    intent: str
    required_facts: tuple[str, ...]
    safe_for_tts: bool
    cooldown_group: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.strip().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TruthSafetyResult:
    accepted: bool
    reason: str


class TruthSafetyGate:
    def validate(
        self,
        candidate: GeneratedDialogueCandidate,
        *,
        context: AmbientContextSnapshot,
        persona: PersonaAuthority,
        recent_digests: tuple[str, ...] = (),
    ) -> TruthSafetyResult:
        text = candidate.text.strip()
        if not text or len(text) > 280:
            return TruthSafetyResult(False, "text_bound")
        sentence_count = len(
            [part for part in re.split(r"[.!?\u3002\uff01\uff1f]+", text) if part.strip()]
        )
        if sentence_count > 3:
            return TruthSafetyResult(False, "sentence_bound")
        lowered = text.casefold()
        if any(marker.casefold() in lowered for marker in _PROHIBITED):
            return TruthSafetyResult(False, "unsupported_capability_claim")
        if any(marker.casefold() in lowered for marker in _INSULTS):
            return TruthSafetyResult(False, "insult")
        if any(marker.casefold() in lowered for marker in _HIGH_RISK_HUMOUR):
            return TruthSafetyResult(False, "high_risk_humour")
        if (
            any(marker.casefold() in lowered for marker in _PERSONA_DRIFT)
            or lowered.count("主人") > 1
            or lowered.count("master") > 1
        ):
            return TruthSafetyResult(False, "persona_drift")
        if not set(candidate.required_facts).issubset(context.true_facts()):
            return TruthSafetyResult(False, "missing_required_fact")
        if candidate.digest in recent_digests:
            return TruthSafetyResult(False, "duplicate")
        if persona.prohibited_drift.allow_persona_selector:
            return TruthSafetyResult(False, "invalid_persona")
        return TruthSafetyResult(True, "accepted")


__all__ = ["GeneratedDialogueCandidate", "TruthSafetyGate", "TruthSafetyResult"]
