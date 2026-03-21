from __future__ import annotations

from dataclasses import dataclass, field


DECISION_HINTS = (
    "结论",
    "决定",
    "最终采用",
    "默认走",
    "改成",
    "不再",
    "统一使用",
    "decision",
    "conclusion",
    "default",
    "switch to",
    "changed to",
    "no longer",
)
REUSABLE_HINTS = (
    "架构",
    "provider",
    "数据库",
    "检索",
    "模式切换",
    "配置原则",
    "browser",
    "persona",
    "memory",
    "rag",
)
STABLE_HINTS = ("长期偏好", "系统边界", "模块职责", "持续有效", "默认", "规则", "policy", "boundary")
HIGH_COST_HINTS = ("fallback", "兼容", "坑点", "限制", "回退", "兼容性", "之前定的", "migration", "provider")
NOISE_HINTS = ("哈哈", "吐槽", "烦死了", "有点乱", "有点烦", "算了", "随便")


@dataclass(slots=True)
class KnowledgeScoreDetails:
    total_score: int
    decision_score: int = 0
    reusable_score: int = 0
    stable_score: int = 0
    forgetting_cost_score: int = 0
    noise_penalty: int = 0
    reasons: list[str] = field(default_factory=list)


class ImportanceScorer:
    def score_knowledge_candidate(
        self,
        *,
        title: str,
        content: str,
        skill_name: str = "",
        task_category: str = "",
    ) -> KnowledgeScoreDetails:
        text = "\n".join(part for part in (title, content, skill_name, task_category) if part).lower()
        details = KnowledgeScoreDetails(total_score=0)

        if any(token.lower() in text for token in DECISION_HINTS):
            details.decision_score = 3
            details.reasons.append("decision_signal")
        if any(token.lower() in text for token in REUSABLE_HINTS):
            details.reusable_score = 2
            details.reasons.append("reusable_signal")
        if any(token.lower() in text for token in STABLE_HINTS):
            details.stable_score = 2
            details.reasons.append("stable_signal")
        if any(token.lower() in text for token in HIGH_COST_HINTS):
            details.forgetting_cost_score = 2
            details.reasons.append("high_forgetting_cost")
        if any(token.lower() in text for token in NOISE_HINTS):
            details.noise_penalty = -4
            details.reasons.append("noise_penalty")

        details.total_score = (
            details.decision_score
            + details.reusable_score
            + details.stable_score
            + details.forgetting_cost_score
            + details.noise_penalty
        )
        return details

    def should_persist_as_memory(self, details: KnowledgeScoreDetails, threshold: int) -> bool:
        return details.total_score >= threshold

    def should_create_decision_card(self, details: KnowledgeScoreDetails, threshold: int) -> bool:
        return details.total_score >= threshold and details.decision_score > 0
