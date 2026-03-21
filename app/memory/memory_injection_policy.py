from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(slots=True)
class MemoryInjectionPolicy:
    include_profile: bool = True
    include_project: bool = True
    include_task: bool = True
    include_semantic: bool = True
    token_budget: int = 360
    per_slot_budget: dict[str, int] = field(default_factory=dict)


def classify_task_category(user_request: str, attachments: Iterable[str] | None = None, *, chosen_skill: str = "") -> str:
    lowered = user_request.lower()
    attached = list(attachments or [])
    if chosen_skill in {"weather", "news", "web_search"}:
        if any(token in lowered for token in ("机票", "价格", "报价", "售价", "多少钱", "折扣", "优惠", "购物")):
            return "shopping"
        return "web_research"
    if chosen_skill == "knowledge_lookup":
        if any(token in lowered for token in ("fairy", "reindex", "persona", "memory", "rag", "fingerprint", "backend", "provider")):
            return "fairy_development"
        return "casual_chat"
    if chosen_skill == "system_ops":
        return "fairy_development"
    if chosen_skill == "direct_answer":
        return "casual_chat"
    if chosen_skill == "web_research_skill" or any(token in lowered for token in ("上网", "联网", "搜索", "网页", "官网", "网址", "b站", "价格", "机票", "粉丝")):
        if any(token in lowered for token in ("机票", "价格", "报价", "售价", "多少钱", "折扣", "优惠", "购物")):
            return "shopping"
        return "web_research"
    if chosen_skill == "agent_shell_skill" or any(token in lowered for token in ("代码", "仓库", "repo", "project", "命令", "终端", "测试", "重构", "修复", "实现", "patch")):
        if "fairy" in lowered or "deskllmchat" in lowered:
            return "fairy_development"
        return "coding_help"
    if chosen_skill == "screen_understanding_skill" or any(token in lowered for token in ("屏幕", "窗口", "界面", "下一步", "点哪里", "点击")):
        return "browser_agent_task"
    if chosen_skill == "document_editor_skill" or attached or any(token in lowered for token in ("文档", "文件", "markdown", "json", "csv", "yaml", ".py")):
        return "coding_help"
    return "casual_chat"


def policy_for_task_category(category: str) -> MemoryInjectionPolicy:
    defaults = {
        "profile": 120,
        "project": 140,
        "task": 120,
        "semantic": 220,
    }
    if category == "fairy_development":
        return MemoryInjectionPolicy(True, True, True, True, 480, {**defaults, "project": 220, "semantic": 260})
    if category == "coding_help":
        return MemoryInjectionPolicy(True, True, True, True, 420, {**defaults, "project": 180, "semantic": 240})
    if category == "web_research":
        return MemoryInjectionPolicy(True, False, True, True, 320, {**defaults, "project": 80, "semantic": 180})
    if category == "shopping":
        return MemoryInjectionPolicy(True, False, True, True, 280, {**defaults, "semantic": 160})
    if category == "browser_agent_task":
        return MemoryInjectionPolicy(True, True, True, True, 320, {**defaults, "task": 180, "semantic": 180})
    return MemoryInjectionPolicy(True, False, True, True, 240, {**defaults, "project": 80, "semantic": 140})
