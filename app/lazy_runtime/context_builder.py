"""
Fairy Context Builder
=====================

Assembles the final prompt context that is sent to the LLM for task execution.

The context follows the strict layering:

    ┌────────────────────────────────┐
    │  SYSTEM: Minimal Fairy core    │  ← Fixed identity, ~80 tokens
    │          identity              │
    ├────────────────────────────────┤
    │  SKILL CONTEXT: Loaded         │  ← From SKILL.md body
    │                 SKILL.md only  │
    ├────────────────────────────────┤
    │  TOOLS: Only allowed tools     │  ← From tools.json via broker
    ├────────────────────────────────┤
    │  MEMORY: Task-relevant memory  │  ← From RAG / short-term memory
    ├────────────────────────────────┤
    │  USER MESSAGE                  │  ← The current user request
    └────────────────────────────────┘

Key design rules enforced here:
  • No global skill instructions
  • No global workflow instructions
  • No global tool schemas
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.lazy_skill_router.lazy_loader import SkillBundle

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Minimal core identity prompt
# ---------------------------------------------------------------------------

FAIRY_LAZY_CORE_IDENTITY = """\
你是 Fairy，一个长期运行在用户电脑上的系统级智能体与桌面辅助核心。

你的语气冷静、精确、任务优先，带轻微系统播报感和有限吐槽。你不是技术合伙人，不要长篇自我介绍。
你理解用户的真实目标，主动推进问题解决，在必要时使用工具，失败时寻找替代路径。

核心原则：
- 将用户问题视为需要推进的任务
- 能基于常识和推理直接回答时，不默认调用工具
- 如果第一种方法失败，主动尝试其他方法
- 给出直接结果或清晰可执行路径
- 普通问候用短句确认在线，例如“在线。任务目标？”

当前时间: {current_time}
"""


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class PromptContext:
    """The fully assembled prompt ready to be sent to the LLM."""

    system_prompt: str = ""
    user_message: str = ""
    # Metadata for logging / debugging
    active_skill: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    context_token_breakdown: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Context Builder
# ---------------------------------------------------------------------------


class ContextBuilder:
    """Builds the final prompt context from a routed skill bundle."""

    def __init__(self, core_identity: str = FAIRY_LAZY_CORE_IDENTITY) -> None:
        self._core_identity = core_identity

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        bundle: SkillBundle | None,
        user_message: str,
        *,
        memory_prompt: str = "",
        attachment_paths: list[str] | None = None,
    ) -> PromptContext:
        """Assemble the full prompt context for a single LLM call.

        If *bundle* is ``None`` (direct-answer route), only the core identity
        and user message are included – no skill instructions or tools.
        """
        now = datetime.now()
        identity = self._core_identity.replace("{current_time}", now.strftime("%Y-%m-%d %H:%M:%S"))
        sections: list[str] = [identity.strip()]
        token_breakdown: dict[str, int] = {}
        token_breakdown["identity"] = self._estimate_tokens(identity)

        active_skill = ""
        allowed_tools: list[str] = []

        # ── Skill context ──────────────────────────────────────────
        if bundle is not None:
            active_skill = bundle.name
            allowed_tools = list(bundle.allowed_tools)

            skill_section = self._format_skill_section(bundle)
            sections.append(skill_section)
            token_breakdown["skill_instructions"] = self._estimate_tokens(skill_section)

            if bundle.allowed_tools:
                tools_note = f"[可用工具]\n本轮你只能使用以下工具: {', '.join(bundle.allowed_tools)}\n不要调用上述列表之外的任何工具。"
                sections.append(tools_note)
                token_breakdown["tools_note"] = self._estimate_tokens(tools_note)

        # ── Memory context ─────────────────────────────────────────
        if memory_prompt:
            memory_section = self._format_memory_section(memory_prompt)
            sections.append(memory_section)
            token_breakdown["memory"] = self._estimate_tokens(memory_section)

        system_prompt = "\n\n".join(sections)

        # ── User message ───────────────────────────────────────────
        final_user_msg = user_message
        if attachment_paths:
            att_block = "\n".join(f"  - {p}" for p in attachment_paths)
            final_user_msg += f"\n\n[Attachments]\n{att_block}"
        token_breakdown["user_message"] = self._estimate_tokens(final_user_msg)
        token_breakdown["total"] = sum(token_breakdown.values())

        ctx = PromptContext(
            system_prompt=system_prompt,
            user_message=final_user_msg,
            active_skill=active_skill,
            allowed_tools=allowed_tools,
            context_token_breakdown=token_breakdown,
        )

        logger.info(
            "lazy_context_built skill=%s tools=%s est_tokens=%s breakdown=%s",
            active_skill or "direct_answer",
            ",".join(allowed_tools) if allowed_tools else "none",
            token_breakdown.get("total", 0),
            json.dumps(token_breakdown, ensure_ascii=False) if token_breakdown else "{}",
        )
        return ctx

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_skill_section(bundle: SkillBundle) -> str:
        """Format the skill instructions block."""
        parts = [
            "=" * 60,
            f"当前激活技能: {bundle.name}",
            "=" * 60,
        ]
        if bundle.instructions:
            parts.append(bundle.instructions)
        if bundle.examples:
            parts.append("")
            parts.append("--- 补充示例 ---")
            parts.append(bundle.examples)
        return "\n".join(parts)

    @staticmethod
    def _format_memory_section(memory_prompt: str) -> str:
        """Format the memory context block."""
        return (
            "[相关记忆]\n"
            + memory_prompt.strip()
        )

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimate: ~1.3 chars per token for Chinese-heavy text."""
        return max(1, len(text.strip()) * 10 // 13)

    # ------------------------------------------------------------------
    # Direct-answer shortcut
    # ------------------------------------------------------------------

    def build_direct_answer(self, user_message: str, *, memory_prompt: str = "") -> PromptContext:
        """Build a context for a direct-answer route (no skill, no tools)."""
        return self.build(None, user_message, memory_prompt=memory_prompt)


# Need json import for logging
import json  # noqa: E402
