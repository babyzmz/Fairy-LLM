from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict


WebAccessMode = Literal[
    "none",
    "search_only",
    "http_fetch",
    "rendered_read",
    "browser_interaction",
    "visual_read",
]

WebIntentType = Literal[
    "structured_lookup",
    "general_web_research",
    "source_constrained_lookup",
    "dynamic_site_lookup",
    "interactive_site_task",
    "visual_page_understanding",
]


class BrowserAction(TypedDict, total=False):
    type: str
    value: str | None
    selector: str | None
    timeout_ms: int | None


BrowserAvailabilityLevel = Literal["full", "partial", "unavailable"]


@dataclass(slots=True)
class BrowserAvailabilityStatus:
    available: bool
    level: BrowserAvailabilityLevel
    reason: str = ""
    fallback_mode: str = "rendered_read"
    executable_path: str = ""
    browser_type: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def availability_level(self) -> BrowserAvailabilityLevel:
        return self.level

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "level": self.level,
            "availability_level": self.level,
            "reason": self.reason,
            "fallback_mode": self.fallback_mode,
            "executable_path": self.executable_path,
            "browser_type": self.browser_type,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(slots=True)
class WebAccessDecision:
    needs_web: bool
    access_mode: WebAccessMode
    intent_type: WebIntentType
    source_name: str | None = None
    source_domain: str | None = None
    preferred_domains: list[str] = field(default_factory=list)
    needs_rendered_page: bool = False
    needs_interaction: bool = False
    needs_visual_reading: bool = False
    reason: str = ""
    target_url: str = ""
    matched_rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "needs_web": self.needs_web,
            "access_mode": self.access_mode,
            "intent_type": self.intent_type,
            "source_name": self.source_name or "",
            "source_domain": self.source_domain or "",
            "preferred_domains": list(self.preferred_domains),
            "needs_rendered_page": self.needs_rendered_page,
            "needs_interaction": self.needs_interaction,
            "needs_visual_reading": self.needs_visual_reading,
            "reason": self.reason,
            "target_url": self.target_url,
            "matched_rules": list(self.matched_rules),
        }


@dataclass(slots=True)
class RetrievalPlan:
    access_mode: WebAccessMode
    primary_queries: list[str] = field(default_factory=list)
    fallback_queries: list[str] = field(default_factory=list)
    preferred_domains: list[str] = field(default_factory=list)
    target_urls: list[str] = field(default_factory=list)
    browser_actions: list[BrowserAction] = field(default_factory=list)
    visual_targets: list[dict[str, Any]] = field(default_factory=list)
    source_constraints: dict[str, Any] = field(default_factory=dict)
    stop_conditions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_mode": self.access_mode,
            "primary_queries": list(self.primary_queries),
            "fallback_queries": list(self.fallback_queries),
            "preferred_domains": list(self.preferred_domains),
            "target_urls": list(self.target_urls),
            "browser_actions": [dict(item) for item in self.browser_actions],
            "visual_targets": [dict(item) for item in self.visual_targets],
            "source_constraints": dict(self.source_constraints),
            "stop_conditions": list(self.stop_conditions),
        }


@dataclass(slots=True)
class VisualReadResult:
    region: str
    summary: str
    confidence: float
    source_url: str = ""
    visual_type: str = "unknown"
    screenshot_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "summary": self.summary,
            "confidence": self.confidence,
            "source_url": self.source_url,
            "visual_type": self.visual_type,
            "screenshot_path": self.screenshot_path,
        }


@dataclass(slots=True)
class WebAccessExecutionResult:
    success: bool
    execution_level: int
    final_mode: WebAccessMode
    decision: WebAccessDecision | None = None
    retrieval_plan: RetrievalPlan | None = None
    search_results: list[dict[str, Any]] = field(default_factory=list)
    opened_pages: list[dict[str, Any]] = field(default_factory=list)
    browser_result: dict[str, Any] = field(default_factory=dict)
    visual_results: list[VisualReadResult] = field(default_factory=list)
    attempted_queries: list[str] = field(default_factory=list)
    used_browser_interaction: bool = False
    used_visual_read: bool = False
    fallback_stage: str = ""
    failure_reason: str = ""
    browser_availability: BrowserAvailabilityStatus | None = None

    @property
    def pages(self) -> list[dict[str, Any]]:
        return self.opened_pages

    @property
    def browser_interaction_used(self) -> bool:
        return self.used_browser_interaction

    @property
    def visual_read_used(self) -> bool:
        return self.used_visual_read

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "execution_level": self.execution_level,
            "final_mode": self.final_mode,
            "decision": self.decision.to_dict() if self.decision is not None else {},
            "retrieval_plan": self.retrieval_plan.to_dict() if self.retrieval_plan is not None else {},
            "search_results": list(self.search_results),
            "opened_pages": list(self.opened_pages),
            "browser_result": dict(self.browser_result),
            "visual_results": [item.to_dict() for item in self.visual_results],
            "attempted_queries": list(self.attempted_queries),
            "used_browser_interaction": self.used_browser_interaction,
            "used_visual_read": self.used_visual_read,
            "fallback_stage": self.fallback_stage,
            "failure_reason": self.failure_reason,
            "browser_availability": self.browser_availability.to_dict() if self.browser_availability is not None else {},
        }
