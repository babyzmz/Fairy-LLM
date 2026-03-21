from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import persona_config
from app.persona.persona_defaults import DEFAULT_FAIRY_NORMAL_PERSONA
from app.persona.persona_guard import GuardReport, PersonaGuard
from app.persona.persona_loader import PersonaLoader
from app.persona.persona_schema import PersonaProfile, RelationshipOverlay
from app.persona.style_renderer import StyleRenderer


class PersonaEngine:
    def __init__(self, source: str | Path | dict[str, Any] | None = None) -> None:
        self.loader_source = source if source is not None else persona_config.default_source
        self.loader: PersonaLoader | None = None
        self._persona: PersonaProfile | None = None
        self._renderer: StyleRenderer | None = None
        self._guard: PersonaGuard | None = None

    @property
    def persona(self) -> PersonaProfile:
        if self._persona is None:
            loader_source = self.loader_source
            self.loader = PersonaLoader(loader_source)
            should_use_defaults = isinstance(loader_source, (str, Path)) and not Path(loader_source).exists()
            self._persona = self.loader.load(
                DEFAULT_FAIRY_NORMAL_PERSONA if should_use_defaults else loader_source
            )
        return self._persona

    @property
    def renderer(self) -> StyleRenderer:
        if self._renderer is None:
            self._renderer = StyleRenderer()
        return self._renderer

    @property
    def guard(self) -> PersonaGuard:
        if self._guard is None:
            self._guard = PersonaGuard(self.renderer)
        return self._guard

    def classify_task_type(self, user_request: str, *, chosen_skill: str = "", task_category: str = "") -> str:
        lowered = user_request.lower()
        if chosen_skill == "document_editor_skill":
            return "file_reading"
        if chosen_skill in {"web_research_skill", "news_intelligence_skill"} or task_category == "web_research":
            return "web_search"
        if chosen_skill == "screen_understanding_skill":
            return "planning"
        if chosen_skill == "agent_shell_skill":
            if any(token in lowered for token in ("debug", "报错", "错误", "异常", "排查", "修复", "fix")):
                return "debugging"
            if any(token in lowered for token in ("计划", "路线", "优先级", "先做什么", "先改", "方案")):
                return "planning"
            return "coding"
        if task_category in {"coding_help", "fairy_development", "browser_agent_task"}:
            return "coding"
        if any(token in lowered for token in ("总结", "摘要", "概括")):
            return "summary"
        return "chat"

    def derive_relationship_overlay(self, user_profile: list[dict[str, Any]] | None = None) -> RelationshipOverlay:
        overlay = self.persona.relationship_overlay_defaults.clone()
        profile_text = " ".join(str(item.get("value", "") or "") for item in (user_profile or []))
        lowered = profile_text.lower()
        if any(token in profile_text for token in ("直接", "少废话", "别绕", "简短")):
            overlay.directness = "very_high"
            overlay.verbosity = "very_low"
            overlay.notes.append("用户偏好更直接、更少废话。")
        if any(token in profile_text for token in ("技术", "工程", "结构化", "代码")):
            overlay.technicality = "very_high"
            overlay.notes.append("用户偏好技术化表达。")
        if any(token in profile_text for token in ("不要安慰", "少安抚", "不需要安慰")):
            overlay.reassurance = "very_low"
            overlay.notes.append("用户不需要情绪安抚。")
        if "别叫主人" in profile_text or "不要叫主人" in profile_text:
            overlay.use_owner_address = False
            overlay.notes.append("避免使用“主人”称呼。")
        elif "主人" in profile_text:
            overlay.use_owner_address = True
        if any(token in lowered for token in ("调侃", "毒舌", "冷幽默")):
            overlay.humour_density = "medium"
        return overlay

    def build_persona_prompt(
        self,
        context: dict[str, Any] | None,
        user_profile: list[dict[str, Any]] | None,
        task_type: str,
        recent_summary: str = "",
    ) -> str:
        context = dict(context or {})
        overlay = self.derive_relationship_overlay(user_profile)
        template = self.persona.prompt_injection_template
        task_bias = self.persona.task_bias.get(task_type, self.persona.task_bias.get("chat", "简洁 + 判断优先"))
        context_policy = self.persona.context_behavior_policy.get(task_type, [])
        recent_summary = (recent_summary or "").strip()
        if len(recent_summary) > persona_config.max_recent_summary_chars:
            recent_summary = recent_summary[: persona_config.max_recent_summary_chars].rstrip() + "..."

        render_data = {
            "name": self.persona.name,
            "mode": self.persona.mode,
            "identity_lines": self._bullet_lines(self.persona.identity),
            "core_trait_lines": self._bullet_lines(self.persona.core_traits),
            "tone_lines": self._bullet_lines(self.persona.tone),
            "speech_rule_lines": self._bullet_lines(self.persona.speech_rules),
            "verdict_density": self.persona.verdict_style.density,
            "verdict_prefixes": " / ".join(self.persona.verdict_style.hard_prefixes),
            "verdict_patterns": " / ".join(self.persona.verdict_style.pattern_preference),
            "humour_lines": self._bullet_lines(self.persona.humour_profile),
            "relationship_lines": self._relationship_lines(overlay),
            "task_type": task_type,
            "task_bias": task_bias,
            "context_policy_lines": self._bullet_lines(context_policy),
            "forbidden_lines": self._bullet_lines(self.persona.forbidden_behaviors),
            "drift_guard_lines": self._bullet_lines(self.persona.drift_guard_rules),
            "recent_summary_block": self._recent_summary_block(recent_summary, context),
        }
        return template.format(**render_data).strip()

    def build_lightweight_prompt(self, task_type: str) -> str:
        task_bias = self.persona.task_bias.get(task_type, self.persona.task_bias.get("chat", "自然 + 推进"))
        prefixes = " / ".join(self.persona.verdict_style.hard_prefixes[:4])
        return (
            "[Persona hint]\n"
            f"- role: {self.persona.name}，长期运行在用户电脑上的个人 AI 助手与技术合伙人\n"
            "- style: 中文、自然、清晰、有判断力、不过度安抚、不卖萌\n"
            f"- verdict_prefixes: {prefixes}\n"
            f"- task_bias: {task_bias}"
        )

    def style_response(
        self,
        text: str,
        *,
        task_type: str,
        user_input: str = "",
        user_profile: list[dict[str, Any]] | None = None,
        recent_summary: str = "",
    ) -> tuple[str, GuardReport]:
        overlay = self.derive_relationship_overlay(user_profile)
        return self.guard.apply(
            text,
            task_type=task_type,
            persona=self.persona,
            overlay=overlay,
            user_input=user_input,
        )

    def _bullet_lines(self, items: list[str]) -> str:
        if not items:
            return "- 无"
        return "\n".join(f"- {item}" for item in items)

    def _relationship_lines(self, overlay: RelationshipOverlay) -> str:
        lines = [
            f"- directness: {overlay.directness}",
            f"- technicality: {overlay.technicality}",
            f"- reassurance: {overlay.reassurance}",
            f"- verbosity: {overlay.verbosity}",
            f"- humour_density: {overlay.humour_density}",
            f"- owner_address: {'allowed' if overlay.use_owner_address else 'disabled'} ({overlay.owner_address_frequency})",
        ]
        lines.extend(f"- {note}" for note in overlay.notes)
        return "\n".join(lines)

    def _recent_summary_block(self, recent_summary: str, context: dict[str, Any]) -> str:
        extra_lines: list[str] = []
        if context.get("current_date"):
            extra_lines.append(f"- current_date: {context['current_date']}")
        if context.get("current_time"):
            extra_lines.append(f"- current_time: {context['current_time']}")
        if context.get("chosen_skill"):
            extra_lines.append(f"- chosen_skill: {context['chosen_skill']}")
        if context.get("memory_category"):
            extra_lines.append(f"- memory_category: {context['memory_category']}")
        if not recent_summary and not extra_lines:
            return ""
        lines = ["\n\n[Runtime context]"]
        if extra_lines:
            lines.extend(extra_lines)
        if recent_summary:
            lines.append("- recent_summary:")
            lines.append(recent_summary)
        return "\n".join(lines)
