"""
Fairy Lazy Skill Router
=======================

Implements the 2-stage routing model:

  Stage 1 – Heuristic + LLM lightweight selection
    Only skill *names* and *one-line descriptions* are sent to the model.
    The model (or heuristic) picks the best candidate skill.

  Stage 2 – Lazy bundle loading
    The selected skill's full SKILL.md, tools.json, and examples.md are loaded
    and passed to the context builder.

The router is intentionally decoupled from the LLM client so that any provider
can be plugged in.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .lazy_loader import SkillBundle, SkillLazyLoader, SkillMetadata
from ..lazy_runtime.legacy_decommissioning import LegacySkillDecommissioner

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocols – keep the router independent of concrete LLM implementations
# ---------------------------------------------------------------------------


class LLMClient(Protocol):
    """Minimal interface the router needs from an LLM."""

    def execute_task(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 260,
        temperature: float = 0.0,
        instruction_label: str = "",
    ) -> Any:
        """Return an object whose ``.text`` attribute contains the raw response."""
        ...


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class LazyRouteDecision:
    """The output of the lazy routing pipeline."""

    skill_name: str
    confidence: float = 0.0
    reason: str = ""
    is_direct_answer: bool = False
    bundle: SkillBundle | None = None


@dataclass
class LazyRouteContext:
    """Optional context from the previous turn to aid routing."""

    previous_skill: str = ""
    screen_followup_remaining: int = 0
    previous_structured: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Prompt for the LLM-based Stage-1 selector
# ---------------------------------------------------------------------------

_STAGE1_SYSTEM_PROMPT = """\
You are a skill router for the Fairy assistant.

Given the user's request and the list of available skills, decide which single skill
is the best match.  If the request can be answered directly from your own knowledge
without any tools, choose "direct_answer".

Respond with a JSON object:
{
  "skill": "<skill-name or direct_answer>",
  "confidence": <0.0-1.0>,
  "reason": "<one sentence>"
}

Available skills:
{skill_manifest}
"""


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class LazySkillRouter:
    """Two-stage skill router with heuristic fast-path and LLM fallback."""

    # Confidence threshold: if the heuristic score exceeds this, skip the LLM call.
    HEURISTIC_CONFIDENCE_THRESHOLD = 0.75

    def __init__(self, loader: SkillLazyLoader, llm: LLMClient | None = None) -> None:
        self._loader = loader
        self._llm = llm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(
        self,
        user_request: str,
        *,
        attachments: list[str] | None = None,
        context: LazyRouteContext | None = None,
        memory_prompt: str = "",
    ) -> LazyRouteDecision:
        """Run the 2-stage routing pipeline and return a ``LazyRouteDecision``.

        The returned decision includes the loaded ``SkillBundle`` (Tier-2) when
        a skill is selected, or ``is_direct_answer=True`` when no skill is needed.
        """
        all_meta = self._loader.all_metadata()

        # Stage 1a: heuristic scoring
        heuristic_scores = self._heuristic_score(user_request, all_meta, context)
        best_name, best_score = max(heuristic_scores.items(), key=lambda kv: kv[1]) if heuristic_scores else ("", 0.0)

        logger.info("lazy_route_stage1_heuristic best=%s score=%.2f", best_name, best_score)

        # Fast path: high-confidence heuristic match → skip LLM
        if best_score >= self.HEURISTIC_CONFIDENCE_THRESHOLD:
            return self._finalize(best_name, best_score, reason="heuristic_match")

        # Stage 1b: LLM-based selection (lightweight – only names + descriptions)
        if self._llm is not None:
            llm_decision = self._llm_select(user_request, all_meta, memory_prompt)
            if llm_decision is not None:
                logger.info(
                    "lazy_route_stage1_llm skill=%s confidence=%.2f reason=%s",
                    llm_decision.skill_name,
                    llm_decision.confidence,
                    llm_decision.reason,
                )
                # Prefer LLM decision if it is more confident
                if llm_decision.confidence > best_score:
                    return self._finalize(
                        llm_decision.skill_name,
                        llm_decision.confidence,
                        reason=llm_decision.reason,
                        is_direct=llm_decision.is_direct_answer,
                    )

        # Fallback: use the best heuristic match even if below threshold
        if best_name:
            return self._finalize(best_name, best_score, reason="heuristic_fallback")

        # Nothing matched at all → direct answer
        return LazyRouteDecision(skill_name="direct_answer", confidence=0.5, reason="no_skill_matched", is_direct_answer=True)

    # ------------------------------------------------------------------
    # Stage 1a – Heuristic scoring
    # ------------------------------------------------------------------

    def _heuristic_score(
        self,
        user_request: str,
        all_meta: list[SkillMetadata],
        context: LazyRouteContext | None,
    ) -> dict[str, float]:
        """Score each skill based on keyword overlap and contextual signals."""
        request_lower = user_request.lower()
        compact = re.sub(r"\s+", "", request_lower)
        tokens = set(re.findall(r"\w+", request_lower))
        scores: dict[str, float] = {}

        for meta in all_meta:
            # Skip legacy skills that should be excluded from routing
            if LegacySkillDecommissioner.should_exclude_from_routing(meta.name):
                logger.info("lazy_route_skip_legacy_skill skill=%s", meta.name)
                continue

            score = 0.0

            # Keyword match (each keyword hit adds weight)
            keyword_hits = sum(1 for kw in meta.trigger_keywords if kw.lower() in request_lower or kw.lower() in compact)
            if meta.trigger_keywords:
                score += 0.5 * min(1.0, keyword_hits / max(2, len(meta.trigger_keywords) * 0.3))

            # Token overlap with description
            desc_tokens = set(re.findall(r"\w+", meta.description.lower()))
            overlap = len(tokens & desc_tokens)
            if desc_tokens:
                score += 0.15 * (overlap / len(desc_tokens))

            # Context continuity bonus
            if context and context.previous_skill == meta.name:
                score += 0.15

            # Screen follow-up bonus
            if context and context.screen_followup_remaining > 0 and meta.name == "screen-understanding":
                score += 0.25

            # Priority normalization (higher priority → small bonus)
            score += 0.05 * (meta.priority / 100.0)

            scores[meta.name] = min(score, 1.0)

        return scores

    # ------------------------------------------------------------------
    # Stage 1b – LLM selection
    # ------------------------------------------------------------------

    def _llm_select(
        self,
        user_request: str,
        all_meta: list[SkillMetadata],
        memory_prompt: str,
    ) -> LazyRouteDecision | None:
        """Ask the LLM to pick a skill using only names + descriptions."""
        manifest_lines = []
        for meta in all_meta:
            # Skip legacy skills that should be excluded from routing
            if LegacySkillDecommissioner.should_exclude_from_routing(meta.name):
                logger.info("lazy_route_llm_skip_legacy_skill skill=%s", meta.name)
                continue
            manifest_lines.append(f"- {meta.name}: {meta.description}")
        manifest_lines.append("- direct_answer: Answer the user directly from your own knowledge without any tools.")
        manifest = "\n".join(manifest_lines)

        system = _STAGE1_SYSTEM_PROMPT.replace("{skill_manifest}", manifest)
        user_msg = f"User request: {user_request}"
        if memory_prompt:
            user_msg += f"\n\nRelevant memory:\n{memory_prompt}"

        try:
            response = self._llm.execute_task(
                system,
                user_msg,
                max_tokens=200,
                temperature=0.0,
                instruction_label="Lazy skill router",
            )
        except Exception:
            logger.exception("lazy_llm_route_failed")
            return None

        return self._parse_llm_response(response.text)

    def _parse_llm_response(self, text: str) -> LazyRouteDecision | None:
        """Parse the JSON response from the LLM."""
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None

        skill = str(data.get("skill", "") or "").strip()
        if not skill:
            return None

        try:
            confidence = float(data.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        reason = str(data.get("reason", "") or "").strip()
        is_direct = skill == "direct_answer"

        return LazyRouteDecision(
            skill_name=skill,
            confidence=confidence,
            reason=reason,
            is_direct_answer=is_direct,
        )

    # ------------------------------------------------------------------
    # Stage 2 – Bundle loading
    # ------------------------------------------------------------------

    def _finalize(
        self,
        skill_name: str,
        confidence: float,
        *,
        reason: str = "",
        is_direct: bool = False,
    ) -> LazyRouteDecision:
        """Load the full skill bundle (Stage 2) and return the final decision."""
        if is_direct or skill_name == "direct_answer":
            return LazyRouteDecision(
                skill_name="direct_answer",
                confidence=confidence,
                reason=reason,
                is_direct_answer=True,
            )

        bundle = self._loader.load_bundle(skill_name)
        if bundle is None:
            logger.warning("lazy_route_finalize bundle_load_failed skill=%s", skill_name)
            return LazyRouteDecision(
                skill_name="direct_answer",
                confidence=0.3,
                reason=f"bundle_load_failed:{skill_name}",
                is_direct_answer=True,
            )

        logger.info(
            "lazy_route_finalize skill=%s confidence=%.2f tools=%d",
            skill_name,
            confidence,
            len(bundle.allowed_tools),
        )
        return LazyRouteDecision(
            skill_name=skill_name,
            confidence=confidence,
            reason=reason,
            bundle=bundle,
        )
