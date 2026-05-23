"""
Fairy Lazy Skill Router
=======================

Model-first routing for bundle skills.

Stage 1:
  LLM semantic selection is primary.
  Heuristics remain as fallback and policy validation.

Stage 2:
  The selected skill bundle is lazily loaded and executed.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .lazy_loader import SkillBundle, SkillLazyLoader, SkillMetadata

logger = logging.getLogger(__name__)


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
    forced_bundle: str = ""
    perception_intent: str = ""
    web_task_type: str = ""
    web_intent_plan: dict[str, Any] = field(default_factory=dict)
    preferred_routes: list[str] = field(default_factory=list)


_STAGE1_SYSTEM_PROMPT = """\
You are the primary semantic skill router for the Fairy assistant.

Decide which single skill best matches the user's request.
If no tool bundle is needed, choose "direct_answer".

Routing rules:
- Prefer semantic intent over keyword matching.
- Choose "direct_answer" for greetings, small talk, explanations, translation,
  writing help, and requests answerable from general knowledge.
- Choose "screen-understanding" only for the currently visible local screen,
  window, screenshot, or UI, or for a clear local GUI follow-up like where to
  click next.
- Do not choose "screen-understanding" just because the text contains words
  like screen or window when those words refer to product specs or abstract topics.
- Choose "web-research" for natural questions that depend on current real-world
  web content, including product specs, comparisons, release status, versions,
  pricing, official docs, or purchase advice, even without explicit web keywords.
- If previous context was a screen task but the new request is a fresh casual turn,
  prefer "direct_answer".

Respond with strict JSON:
{
  "skill": "<skill-name or direct_answer>",
  "confidence": <0.0-1.0>,
  "reason": "<one sentence>"
}

Available skills:
{skill_manifest}
"""


class LazySkillRouter:
    """Model-first skill router with heuristic fallback and policy validation."""

    HEURISTIC_CONFIDENCE_THRESHOLD = 0.75
    HEURISTIC_MIN_SKILL_CONFIDENCE = 0.25
    LLM_PRIMARY_MIN_CONFIDENCE = 0.20

    def __init__(self, loader: SkillLazyLoader, llm: LLMClient | None = None) -> None:
        self._loader = loader
        self._llm = llm

    def route(
        self,
        user_request: str,
        *,
        attachments: list[str] | None = None,
        context: LazyRouteContext | None = None,
        memory_prompt: str = "",
    ) -> LazyRouteDecision:
        """Run routing and return a ``LazyRouteDecision``."""
        all_meta = self._loader.all_metadata()
        attachments = list(attachments or [])
        looks_local_visual = self._looks_like_local_visual_request(
            user_request,
            attachments=attachments,
            context=context,
        )
        looks_web = self._looks_like_web_research_request(user_request, context)

        heuristic_scores = self._heuristic_score(user_request, all_meta, context, attachments)
        best_name, best_score = max(heuristic_scores.items(), key=lambda kv: kv[1]) if heuristic_scores else ("", 0.0)
        logger.info("lazy_route_stage1_heuristic best=%s score=%.2f", best_name, best_score)

        llm_decision: LazyRouteDecision | None = None
        if self._llm is not None:
            llm_decision = self._llm_select(
                user_request,
                all_meta,
                memory_prompt,
                context=context,
                attachments=attachments,
            )
            llm_decision = self._apply_policy_guard(
                llm_decision,
                user_request,
                context=context,
                attachments=attachments,
            )
            if llm_decision is not None:
                logger.info(
                    "lazy_route_stage1_llm skill=%s confidence=%.2f reason=%s",
                    llm_decision.skill_name,
                    llm_decision.confidence,
                    llm_decision.reason,
                )
                if llm_decision.confidence >= self.LLM_PRIMARY_MIN_CONFIDENCE or best_score < self.HEURISTIC_CONFIDENCE_THRESHOLD:
                    return self._finalize(
                        llm_decision.skill_name,
                        llm_decision.confidence,
                        reason=llm_decision.reason,
                        is_direct=llm_decision.is_direct_answer,
                    )

        if best_score >= self.HEURISTIC_CONFIDENCE_THRESHOLD:
            return self._finalize(best_name, best_score, reason="heuristic_fallback")

        if llm_decision is not None:
            return self._finalize(
                llm_decision.skill_name,
                llm_decision.confidence,
                reason=llm_decision.reason,
                is_direct=llm_decision.is_direct_answer,
            )

        if best_name and best_score >= self.HEURISTIC_MIN_SKILL_CONFIDENCE:
            return self._finalize(best_name, best_score, reason="heuristic_fallback")

        if looks_local_visual:
            return self._finalize(
                "screen-understanding",
                0.42,
                reason="policy_fallback:local_visual_request",
            )

        if looks_web:
            return self._finalize(
                "web-research",
                0.42,
                reason="policy_fallback:web_request",
            )

        return LazyRouteDecision(
            skill_name="direct_answer",
            confidence=0.5,
            reason="no_skill_matched",
            is_direct_answer=True,
        )

    def _heuristic_score(
        self,
        user_request: str,
        all_meta: list[SkillMetadata],
        context: LazyRouteContext | None,
        attachments: list[str],
    ) -> dict[str, float]:
        """Score each skill from weak signals. Used only as fallback."""
        request_lower = user_request.lower()
        compact = re.sub(r"\s+", "", request_lower)
        tokens = set(re.findall(r"\w+", request_lower))
        looks_social = self._looks_like_social_turn(user_request)
        looks_local_visual = self._looks_like_local_visual_request(
            user_request,
            attachments=attachments,
            context=context,
        )
        looks_web = self._looks_like_web_research_request(user_request, context)
        scores: dict[str, float] = {}

        for meta in all_meta:
            score = 0.0

            keyword_hits = sum(1 for kw in meta.trigger_keywords if kw.lower() in request_lower or kw.lower() in compact)
            if meta.trigger_keywords:
                score += 0.5 * min(1.0, keyword_hits / max(2, len(meta.trigger_keywords) * 0.3))

            desc_tokens = set(re.findall(r"\w+", meta.description.lower()))
            overlap = len(tokens & desc_tokens)
            if desc_tokens:
                score += 0.15 * (overlap / len(desc_tokens))

            if context and context.previous_skill == meta.name and not looks_social:
                score += 0.15

            if meta.name == "web-research" and looks_web:
                score += 0.18

            if meta.name == "screen-understanding" and looks_local_visual:
                score += 0.18

            if (
                meta.name == "screen-understanding"
                and context
                and context.screen_followup_remaining > 0
                and looks_local_visual
            ):
                score += 0.25

            score += 0.05 * (meta.priority / 100.0)

            if meta.name == "screen-understanding" and not looks_local_visual:
                score *= 0.35
            if meta.name == "web-research" and not looks_web:
                score *= 0.90
            if looks_social:
                score *= 0.65

            scores[meta.name] = min(score, 1.0)

        return scores

    def _looks_like_explicit_web_request(self, user_request: str, context: LazyRouteContext | None) -> bool:
        if context and str(context.forced_bundle or "").strip() == "web-research":
            return True
        lowered = str(user_request or "").strip().lower()
        explicit_terms = (
            "http://",
            "https://",
            "www.",
            "\u5b98\u7f51",
            "\u7f51\u9875",
            "\u7f51\u7ad9",
            "\u94fe\u63a5",
            "url",
            "link",
            "browse",
            "browser",
            "\u8054\u7f51",
            "\u4e0a\u7f51",
            "\u6253\u5f00\u7f51\u9875",
            "\u6253\u5f00\u7f51\u7ad9",
        )
        return any(term in lowered for term in explicit_terms)

    def _llm_select(
        self,
        user_request: str,
        all_meta: list[SkillMetadata],
        memory_prompt: str,
        *,
        context: LazyRouteContext | None,
        attachments: list[str],
    ) -> LazyRouteDecision | None:
        """Ask the LLM to pick a skill using names and descriptions only."""
        manifest_lines = [f"- {meta.name}: {meta.description}" for meta in all_meta]
        manifest_lines.append("- direct_answer: Answer the user directly from your own knowledge without any tools.")
        manifest = "\n".join(manifest_lines)
        allowed_skills = {meta.name for meta in all_meta}
        allowed_skills.add("direct_answer")

        system = _STAGE1_SYSTEM_PROMPT.replace("{skill_manifest}", manifest)
        routing_context = {
            "previous_skill": str(context.previous_skill or "") if context else "",
            "screen_followup_remaining": int(context.screen_followup_remaining or 0) if context else 0,
            "forced_bundle": str(context.forced_bundle or "") if context else "",
            "perception_intent": str(context.perception_intent or "") if context else "",
            "web_task_type": str(context.web_task_type or "") if context else "",
            "web_intent_plan": dict(context.web_intent_plan or {}) if context else {},
            "preferred_routes": list(context.preferred_routes or []) if context else [],
            "has_attachments": bool(attachments),
            "attachment_count": len(attachments),
        }
        user_msg = (
            f"User request: {user_request}\n\n"
            f"Routing context:\n{json.dumps(routing_context, ensure_ascii=False)}"
        )
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

        return self._parse_llm_response(response.text, allowed_skills)

    def _parse_llm_response(self, text: str, allowed_skills: set[str]) -> LazyRouteDecision | None:
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
        if not skill or skill not in allowed_skills:
            return None

        try:
            confidence = float(data.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        reason = str(data.get("reason", "") or "").strip()
        return LazyRouteDecision(
            skill_name=skill,
            confidence=confidence,
            reason=reason,
            is_direct_answer=skill == "direct_answer",
        )

    def _apply_policy_guard(
        self,
        decision: LazyRouteDecision | None,
        user_request: str,
        *,
        context: LazyRouteContext | None,
        attachments: list[str],
    ) -> LazyRouteDecision | None:
        """Use rules as validation and fallback, not as the primary router."""
        if decision is None:
            return None

        if self._looks_like_social_turn(user_request):
            if decision.skill_name != "direct_answer":
                return LazyRouteDecision(
                    skill_name="direct_answer",
                    confidence=max(decision.confidence, 0.80),
                    reason="policy:casual_turn_prefers_direct_answer",
                    is_direct_answer=True,
                )
            return decision

        looks_local_visual = self._looks_like_local_visual_request(
            user_request,
            attachments=attachments,
            context=context,
        )
        looks_web = self._looks_like_web_research_request(user_request, context)

        if decision.skill_name == "screen-understanding" and not looks_local_visual:
            if looks_web:
                return LazyRouteDecision(
                    skill_name="web-research",
                    confidence=max(decision.confidence, 0.65),
                    reason="policy:screen_skill_requires_local_visual_context",
                    is_direct_answer=False,
                )
            return LazyRouteDecision(
                skill_name="direct_answer",
                confidence=max(decision.confidence, 0.55),
                reason="policy:screen_skill_requires_local_visual_context",
                is_direct_answer=True,
            )

        if decision.skill_name == "direct_answer" and looks_local_visual and not looks_web:
            return LazyRouteDecision(
                skill_name="screen-understanding",
                confidence=max(decision.confidence, 0.60),
                reason="policy:local_visual_request_needs_visual_skill",
                is_direct_answer=False,
            )

        return decision

    @staticmethod
    def _looks_like_social_turn(user_request: str) -> bool:
        lowered = str(user_request or "").strip().lower()
        if not lowered or len(lowered) > 24:
            return False
        social_terms = {
            "\u4f60\u597d",
            "\u55e8",
            "\u54c8\u55bd",
            "\u65e9\u4e0a\u597d",
            "\u4e0b\u5348\u597d",
            "\u665a\u4e0a\u597d",
            "\u5728\u5417",
            "\u5728\u4e0d\u5728",
            "\u8c22\u8c22",
            "\u591a\u8c22",
            "\u4f60\u662f\u8c01",
            "hi",
            "hello",
            "hey",
            "thanks",
            "thank you",
            "who are you",
            "are you there",
        }
        return lowered in social_terms

    def _looks_like_local_visual_request(
        self,
        user_request: str,
        *,
        attachments: list[str],
        context: LazyRouteContext | None,
    ) -> bool:
        if attachments:
            return True
        lowered = str(user_request or "").strip().lower()
        if not lowered:
            return False

        explicit_visual_phrases = (
            "\u5f53\u524d\u5c4f\u5e55",
            "\u6211\u7684\u5c4f\u5e55",
            "\u8fd9\u4e2a\u5c4f\u5e55",
            "\u5c4f\u5e55\u4e0a",
            "\u5206\u6790\u6211\u7684\u5c4f\u5e55",
            "\u770b\u4e00\u4e0b\u6211\u7684\u5c4f\u5e55",
            "\u5f53\u524d\u7a97\u53e3",
            "\u6211\u7684\u7a97\u53e3",
            "\u8fd9\u4e2a\u7a97\u53e3",
            "\u7a97\u53e3\u91cc",
            "\u5f53\u524d\u754c\u9762",
            "\u8fd9\u4e2a\u754c\u9762",
            "\u754c\u9762\u4e0a",
            "\u8fd9\u5f20\u622a\u56fe",
            "\u8fd9\u4e2a\u622a\u56fe",
            "\u622a\u56fe\u91cc",
            "current screen",
            "my screen",
            "this screen",
            "what's on my screen",
            "analyze my screen",
            "current window",
            "my window",
            "this window",
            "this screenshot",
            "in the screenshot",
        )
        if any(phrase in lowered for phrase in explicit_visual_phrases):
            return True

        step_terms = (
            "\u4e0b\u4e00\u6b65",
            "\u8fd9\u6b65",
            "\u600e\u4e48\u7ee7\u7eed",
            "\u63a5\u4e0b\u6765\u600e\u4e48\u505a",
            "\u73b0\u5728\u8981\u505a\u4ec0\u4e48",
            "\u70b9\u54ea\u91cc",
            "\u70b9\u54ea\u4e2a",
            "\u54ea\u4e2a\u6309\u94ae",
            "what should i do next",
            "where do i click",
            "which button",
        )
        ui_anchor_terms = (
            "\u6309\u94ae",
            "\u83dc\u5355",
            "\u56fe\u6807",
            "\u8f93\u5165\u6846",
            "\u5bf9\u8bdd\u6846",
            "\u7a97\u53e3",
            "\u754c\u9762",
            "button",
            "menu",
            "icon",
            "dialog",
            "window",
            "ui",
        )
        if any(step in lowered for step in step_terms) and any(anchor in lowered for anchor in ui_anchor_terms):
            return True

        if context and context.previous_skill == "screen-understanding" and context.screen_followup_remaining > 0:
            return any(step in lowered for step in step_terms)

        return False

    def _looks_like_web_research_request(self, user_request: str, context: LazyRouteContext | None) -> bool:
        if context and str(context.forced_bundle or "").strip() == "web-research":
            return True
        if context and (str(context.web_task_type or "").strip() or bool(context.web_intent_plan)):
            return True
        lowered = str(user_request or "").strip().lower()
        if not lowered:
            return False
        if self._looks_like_explicit_web_request(user_request, context):
            return True

        natural_web_phrases = (
            "\u53c2\u6570",
            "\u914d\u7f6e",
            "\u89c4\u683c",
            "\u5bf9\u6bd4",
            "\u533a\u522b",
            "\u600e\u4e48\u9009",
            "\u503c\u4e0d\u503c\u5f97\u4e70",
            "\u503c\u5f97\u4e70\u5417",
            "\u5c4f\u5e55\u591a\u5927",
            "\u591a\u91cd",
            "\u6709\u51e0\u4e2a\u7248\u672c",
            "\u51fa\u4e86\u5417",
            "\u53d1\u5e03\u4e86\u5417",
            "\u4ec0\u4e48\u65f6\u5019\u53d1\u5e03",
            "\u4ef7\u683c",
            "\u5b98\u7f51",
            "\u6587\u6863",
            "\u8bc4\u6d4b",
            "compare",
            "vs",
            "which one",
            "how to choose",
            "worth buying",
            "screen size",
            "how big",
            "versions",
            "released",
            "out yet",
            "price",
            "official docs",
            "documentation",
        )
        return any(phrase in lowered for phrase in natural_web_phrases)

    def _finalize(
        self,
        skill_name: str,
        confidence: float,
        *,
        reason: str = "",
        is_direct: bool = False,
    ) -> LazyRouteDecision:
        """Load the full skill bundle and return the final decision."""
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
