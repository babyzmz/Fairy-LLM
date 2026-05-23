from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.persona.persona_schema import PersonaProfile, RelationshipOverlay
from app.persona.style_renderer import StyleRenderer


@dataclass(slots=True)
class GuardReport:
    emotional_score: int = 0
    cute_score: int = 0
    toxicity_score: int = 0
    customer_service_score: int = 0
    verdict_present: bool = False
    modifications: list[str] = field(default_factory=list)


class PersonaGuard:
    def __init__(self, renderer: StyleRenderer) -> None:
        self.renderer = renderer

    def evaluate(self, text: str, persona: PersonaProfile) -> GuardReport:
        guard = persona.anti_drift_guard
        verdict_prefixes = tuple(str(prefix) for prefix in persona.verdict_style.hard_prefixes)
        stripped = text.strip()
        return GuardReport(
            emotional_score=self._count_markers(text, guard.emotional_markers) + text.count("！") + text.count("!"),
            cute_score=self._count_markers(text, guard.cute_markers),
            toxicity_score=self._count_markers(text, guard.toxic_markers),
            customer_service_score=self._count_markers(text, guard.customer_service_markers),
            verdict_present=any(self._has_verdict_prefix(stripped, prefix) for prefix in verdict_prefixes),
        )

    def apply(
        self,
        text: str,
        *,
        task_type: str,
        persona: PersonaProfile,
        overlay: RelationshipOverlay,
        user_input: str = "",
    ) -> tuple[str, GuardReport]:
        current = text.strip()
        report = self.evaluate(current, persona)
        if not current:
            return current, report

        if report.cute_score and persona.anti_drift_guard.block_cute_style:
            current = self._strip_cute_style(current)
            report.modifications.append("cute_style_removed")
        if report.customer_service_score and persona.anti_drift_guard.block_customer_service_tone:
            current = self._strip_customer_service_tone(current)
            report.modifications.append("customer_service_tone_rewritten")
        if report.toxicity_score and persona.anti_drift_guard.reduce_excessive_toxicity:
            current = self._soften_toxicity(current)
            report.modifications.append("toxicity_softened")
        if report.emotional_score >= 3 and persona.anti_drift_guard.soften_emotional_output:
            current = self._cool_down_emotionality(current)
            report.modifications.append("emotionality_reduced")

        current = self.renderer.render(
            current,
            task_type=task_type,
            persona=persona,
            overlay=overlay,
            user_input=user_input,
        )
        final_report = self.evaluate(current, persona)
        if (
            persona.anti_drift_guard.enforce_verdict_presence
            and not final_report.verdict_present
            and self._should_enforce_verdict(task_type, current)
        ):
            current = self._add_verdict_prefix(current, task_type=task_type, user_input=user_input)
            report.modifications.append("verdict_prefix_added")

        final_report = self.evaluate(current, persona)
        final_report.modifications = report.modifications
        return current, final_report

    def _count_markers(self, text: str, markers: list[str]) -> int:
        lowered = text.lower()
        return sum(lowered.count(str(marker).lower()) for marker in markers)

    def _has_verdict_prefix(self, text: str, prefix: str) -> bool:
        if not prefix:
            return False
        candidates = (text, text.removeprefix("可以。").lstrip())
        for candidate in candidates:
            if candidate == prefix:
                return True
            if candidate.startswith(prefix):
                suffix = candidate[len(prefix) : len(prefix) + 1]
                if suffix in {"", "：", ":", "。", "，", ",", "、", " "}:
                    return True
        return False

    def _should_enforce_verdict(self, task_type: str, text: str) -> bool:
        if not text.strip():
            return False
        return task_type in {"chat", "coding", "debugging", "planning", "web_search", "file_reading", "summary"}

    def _add_verdict_prefix(self, text: str, *, task_type: str, user_input: str = "") -> str:
        current = text.strip()
        lowered_input = user_input.lower()

        if current.startswith("可以。") and any(token in user_input for token in ("夸", "认可", "鼓励")):
            rest = current.removeprefix("可以。").strip()
            return f"可以。判断：{rest}" if rest else "可以。判断成立。"

        if task_type == "planning":
            if any(token in user_input for token in ("是不是", "是否", "能不能", "要不要", "该不该")):
                return f"肯定：{current}"
            return f"判断：{current}"
        if task_type in {"coding", "debugging", "summary"}:
            return f"结论：{current}"
        if task_type in {"web_search", "file_reading"}:
            return f"确认：{current}"
        if any(token in lowered_input for token in ("why", "how", "what")):
            return f"判断：{current}"
        return f"确认：{current}"

    def _strip_cute_style(self, text: str) -> str:
        replacements = {
            "好呀": "可以",
            "宝宝": "",
            "宝贝": "",
            "呀亲": "",
            "亲亲": "",
            "人家觉得": "我的判断是",
            "嘛": "。",
            "主人~": "你",
        }
        current = text
        for source, target in replacements.items():
            current = current.replace(source, target)
        return re.sub(r"(?:^|\s)亲(?=\s|$)", " ", current).strip()

    def _strip_customer_service_tone(self, text: str) -> str:
        replacements = {
            "很高兴为您服务": "继续处理当前任务",
            "请问还有什么可以帮助您": "如果需要，我可以继续往下处理",
            "感谢您的支持": "继续推进当前任务更有价值",
            "祝您生活愉快": "先处理到这里",
        }
        current = text
        for source, target in replacements.items():
            current = current.replace(source, target)
        return current

    def _soften_toxicity(self, text: str) -> str:
        replacements = {
            "蠢": "低效",
            "废物": "不合格方案",
            "白痴": "不合理",
            "弱智": "低质量",
            "垃圾": "噪声",
        }
        current = text
        for source, target in replacements.items():
            current = current.replace(source, target)
        return current

    def _cool_down_emotionality(self, text: str) -> str:
        cooled = re.sub(r"[!！]{2,}", "。", text)
        cooled = cooled.replace("真的非常非常", "确实")
        cooled = cooled.replace("超级难过", "不理想")
        cooled = cooled.replace("太感动了", "可以确认")
        cooled = cooled.replace("呜呜", "")
        return cooled.strip()
