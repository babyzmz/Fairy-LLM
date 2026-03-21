"""Capability Registry — maps skill names to agent backends.

Single source of truth for skill → agent mappings.
Used by InvocationService to dispatch execution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SkillCapability:
    """A registered capability entry."""
    skill_name: str
    display_name: str
    description: str
    agent_module: str
    agent_class: str
    is_pure_llm: bool = False
    required_tools: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


class CapabilityRegistry:
    """Registry mapping skill names to agent backends."""

    def __init__(self) -> None:
        self._caps: dict[str, SkillCapability] = {}

    def register(self, cap: SkillCapability) -> None:
        self._caps[cap.skill_name] = cap

    def get(self, skill_name: str) -> SkillCapability | None:
        return (
            self._caps.get(skill_name)
            or self._caps.get(skill_name.replace("-", "_"))
            or self._caps.get(skill_name.replace("_", "-"))
        )

    def has_agent_backend(self, skill_name: str) -> bool:
        cap = self.get(skill_name)
        return cap is not None and not cap.is_pure_llm

    def list_all(self) -> list[SkillCapability]:
        return list(self._caps.values())

    def load_agent_class(self, skill_name: str) -> type | None:
        cap = self.get(skill_name)
        if cap is None or cap.is_pure_llm:
            return None
        try:
            import importlib
            mod = importlib.import_module(cap.agent_module)
            return getattr(mod, cap.agent_class)
        except Exception as exc:
            logger.error("capability_load_error skill=%s error=%s", skill_name, exc)
            return None

    @classmethod
    def default(cls) -> "CapabilityRegistry":
        """Create the default registry with all built-in capabilities."""
        r = cls()
        r.register(SkillCapability(
            skill_name="realtime_lookup",
            display_name="Realtime Lookup",
            description=(
                "Autonomous 5-stage realtime data lookup: weather, time, "
                "crypto, stock, exchange rate, fuel price, sports scores."
            ),
            agent_module="app.agents.realtime_lookup.agent",
            agent_class="RealtimeLookupAgent",
            is_pure_llm=False,
            required_tools=["search_web"],
            tags=["realtime", "data", "factual"],
        ))
        r.register(SkillCapability(
            skill_name="web_research",
            display_name="Web Research",
            description=(
                "Open-web research: search, browse, extract, cross-validate, "
                "and produce cited answers. Use for research, news, comparison, "
                "verification, and deep-explanation tasks."
            ),
            agent_module="app.agents.web_research.agent",
            agent_class="WebResearchAgent",
            is_pure_llm=False,
            required_tools=["search_web", "fetch_page"],
            tags=["research", "web", "factual", "news", "comparison"],
        ))
        for name, display, desc in [
            ("web_research_legacy",   "Web Research (LLM)",  "General web research (LLM fallback)."),
            ("news_intelligence",    "News Intelligence",   "Tech news briefing."),
            ("document_editing",     "Document Editing",    "Document read/write."),
            ("screen_understanding", "Screen Understanding","Visual screen analysis."),
            ("terminal_agent",       "Terminal Agent",      "Shell command execution."),
        ]:
            r.register(SkillCapability(
                skill_name=name, display_name=display, description=desc,
                agent_module="", agent_class="",
                is_pure_llm=True, tags=["llm"],
            ))
        logger.info("capability_registry_initialized caps=%d", len(r._caps))
        return r


_default: CapabilityRegistry | None = None


def get_registry() -> CapabilityRegistry:
    """Lazy singleton — returns the shared default registry."""
    global _default
    if _default is None:
        _default = CapabilityRegistry.default()
    return _default
