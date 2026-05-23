from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from app.models.skill_result import SkillResult
from app.models.tool_result import ToolResult
from app.skills.bundles.runtime_types import BundleRuntimeServices


logger = logging.getLogger(__name__)
BUNDLE_NAME = "web-research"
_CONTINUE_MARKERS = ("继续", "继续找", "继续看", "再找找", "再看看", "再查查", "继续浏览")
_EXPAND_SOURCE_MARKERS = ("换个来源继续找", "换个来源", "换个网站继续找")
_TASK_MARKERS: dict[str, tuple[str, ...]] = {
    "specs": ("参数", "配置", "规格", "技术规格", "spec", "specs", "specifications", "technical specifications"),
    "compare": ("区别", "差异", "对比", "比较", " vs ", "versus", "compare", "comparison"),
    "news": ("新闻", "新消息", "今天有什么新消息", "今天有什么新闻", "latest", "news", "newsroom", "blog", "press"),
    "release": ("发布了吗", "什么时候发布", "发布时间", "发售了吗", "release date", "released", "announced"),
    "product_lookup": ("产品页", "产品信息", "产品介绍", "official product"),
    "general_info": ("官网", "是什么", "是干嘛的", "介绍", "介绍一下", "怎么用", "如何", "about", "overview", "what is"),
}
_LOW_QUALITY_URL_TERMS = ("support", "help", "forum", "community", "zhihu", "jingyan", "baidu")
_ANSWER_PROMPT = """You are Fairy's web-research answer synthesizer.
Return strict JSON with keys: summary, recommendation, response_text.
Write concise Chinese. response_text should be 2-4 natural sentences.
Use only the supplied page content."""
_TASK_PLAN_PROMPT = """You are the task planner for Fairy's web-research bundle.
Plan the web research task from natural language, using semantic understanding instead of keyword matching.
Allowed task_type values:
- specs
- compare
- news
- release
- product_lookup
- general_info

Guidelines:
- Questions about size, versions, weight, battery, display, or specs usually map to specs.
- Questions about how to choose, which is better, differences, or worth buying between options usually map to compare.
- Questions about whether something is out yet, announced, launched, or available usually map to release.
- Questions about what an official site/docs page does usually map to general_info.
- Questions about a specific product page, feature set, or purchase-fit judgment for one product usually map to product_lookup.
- Keep search_query concise and web-friendly.

Return strict JSON only with keys:
{
  "task_type": "specs" | "compare" | "news" | "release" | "product_lookup" | "general_info",
  "entity": "<main entity or product>",
  "search_query": "<best initial web query>",
  "answer_focus": "<what the user actually wants answered>",
  "reason": "<short reason>"
}"""
_ACTION_PROMPT = """You are the web-research bundle runtime for Fairy.
Drive a bounded web research loop using strict JSON only.

Available actions:
- discover_sources
- open_source
- open_link
- revise_query
- answer
- fail

Rules:
- Prefer discover_sources before any page open when there are no sources.
- Prefer open_link over revise_query when a useful link exists.
- Do not answer from a SERP, generic homepage, or support/forum/help page unless the user asked for support.
- Only answer when the current page clearly satisfies the task.

Return exactly one JSON object:
{
  "action": "discover_sources" | "open_source" | "open_link" | "revise_query" | "answer" | "fail",
  "query": "<query for discover_sources>",
  "source_index": 0,
  "link_id": 0,
  "revised_query": "<revised query>",
  "reason": "<short reason>",
  "stop_reason": "<short stop reason>",
  "citation_page_ids": [0],
  "response_text": "<final answer text when action=answer>",
  "failure_reason": "<failure reason when action=fail>"
}
Omit fields that do not apply."""


class ToolLoopWebResearchRunner:
    MAX_ACTIONS = 6
    MAX_DISCOVER_CALLS = 2
    MAX_PAGE_OPENS = 4
    MAX_SAME_DOMAIN_HOPS = 2
    MAX_REVISE_QUERY = 1

    def __init__(
        self,
        *,
        services: BundleRuntimeServices,
        allowed_tools: list[str],
        memory_context: str,
        route_context: object | None,
        prompt_context: object | None,
    ) -> None:
        self.services = services
        self.llm = services.llm
        self.web = services.web_runtime
        self.allowed_tools = list(allowed_tools)
        self.memory_context = str(memory_context or "").strip()
        self.route_context = route_context
        self.prompt_context = prompt_context

    def execute(self, user_request: str) -> SkillResult:
        if self.web is None:
            return SkillResult(
                skill_name=BUNDLE_NAME,
                success=False,
                summary="Web runtime unavailable.",
                response_text="当前网页浏览运行时不可用。",
                structured={"bundle_name": BUNDLE_NAME, "failure_reason": "web_runtime_unavailable"},
            )
        state = self._build_state(user_request)
        invalid_count = 0
        for _ in range(self.MAX_ACTIONS):
            allowed = self._allowed_actions(state)
            action = self._model_action(state, allowed)
            if not self._valid_action(action, state, allowed):
                invalid_count += 1
                action = self._fallback_action(state, allowed)
            if not self._valid_action(action, state, allowed):
                invalid_count += 1
            if invalid_count >= 2 and not self._valid_action(action, state, allowed):
                return self._failure(
                    state,
                    "invalid_model_output_fallback_exhausted",
                    "网页浏览决策连续失败，无法继续稳定执行。",
                )
            state["web_loop_action_count"] += 1
            state["last_model_action"] = str(action.get("action") or "").strip()
            self._emit_progress("model_decision_emitted", action=state["last_model_action"], task_type=state["task_type"])
            result = self._step(state, action)
            if isinstance(result, SkillResult):
                return result
        return self._failure(
            state,
            "browse_navigation_exhausted",
            "网页浏览已达到当前步数上限。",
            stop_reason="step_limit",
            stop_detail="action_limit_reached",
        )

    def _build_state(self, user_request: str) -> dict[str, Any]:
        route_context = self.route_context
        previous_structured = dict(getattr(route_context, "previous_structured", {}) or {}) if route_context else {}
        web_context = dict(getattr(route_context, "web_context", {}) or previous_structured.get("web_context") or {}) if route_context else {}
        continuation_mode = self._continuation_mode(user_request)
        route_plan = dict(getattr(route_context, "web_intent_plan", {}) or {}) if route_context else {}
        task_plan = self._plan_research_task(user_request, route_plan)
        task_type = (
            str(route_plan.get("task_type") or "").strip()
            or str(task_plan.get("task_type") or "").strip()
            or str(getattr(route_context, "web_task_type", "") or "").strip()
            or str(web_context.get("last_task_type") or "").strip()
            or self._infer_task_type(user_request)
        )
        if continuation_mode != "none" and str(web_context.get("last_query") or "").strip():
            effective_query = str(web_context.get("last_query") or "").strip()
        else:
            effective_query = str(route_plan.get("search_query") or task_plan.get("search_query") or user_request or "").strip()
        state = {
            "user_query": str(user_request or "").strip(),
            "effective_query": effective_query,
            "task_type": task_type,
            "entity": str(route_plan.get("entity") or task_plan.get("entity") or "").strip() or self._infer_entity(effective_query, task_type),
            "task_plan": {
                "task_type": task_type,
                "entity": str(route_plan.get("entity") or task_plan.get("entity") or "").strip(),
                "search_query": str(route_plan.get("search_query") or task_plan.get("search_query") or "").strip(),
                "answer_focus": str(route_plan.get("answer_focus") or task_plan.get("answer_focus") or "").strip(),
                "reason": str(route_plan.get("reason") or task_plan.get("reason") or "").strip(),
            },
            "continuation_mode": continuation_mode,
            "source_candidates": list(web_context.get("last_sources") or []) if continuation_mode != "none" else [],
            "attempted_source_indices": list(web_context.get("last_attempted_source_indices") or []),
            "current_source_index": int(web_context.get("last_current_source_index") or 0),
            "current_page_snapshot": dict(web_context.get("last_page") or {}) if continuation_mode == "continue" else {},
            "current_page_candidate_links": list(web_context.get("last_links") or []) if continuation_mode == "continue" else [],
            "current_page_answer_context": dict(web_context.get("last_page_answer_context") or {}) if continuation_mode == "continue" else {},
            "current_page_url": str(web_context.get("last_final_page_url") or "").strip() if continuation_mode == "continue" else "",
            "current_page_type": str(web_context.get("last_final_page_type") or "").strip() if continuation_mode == "continue" else "",
            "visited_urls": set(
                str(item).strip()
                for item in list(web_context.get("visited_urls") or [])
                if str(item).strip()
            ),
            "opened_pages": [],
            "search_queries_attempted": list(web_context.get("last_search_queries_attempted") or []),
            "provider_diagnostics": list(web_context.get("last_provider_diagnostics") or []),
            "provider_quality_state": str(web_context.get("last_provider_quality_state") or "").strip(),
            "provider_quality_gate_triggered": bool(web_context.get("last_provider_quality_gate_triggered")),
            "low_signal_fallback_to_serp_used": bool(web_context.get("last_low_signal_fallback_to_serp_used")),
            "revised_query": str(web_context.get("last_revised_query") or "").strip() if continuation_mode != "none" else "",
            "discover_calls": int(web_context.get("last_discover_calls") or 0) if continuation_mode != "none" else 0,
            "revise_count": 1 if continuation_mode != "none" and str(web_context.get("last_revised_query") or "").strip() else 0,
            "page_open_count": int(web_context.get("last_page_open_count") or 0) if continuation_mode != "none" else 0,
            "same_domain_hops": {},
            "selected_links": [],
            "stop_reason": "",
            "stop_detail": "",
            "failure_reason": "",
            "web_loop_action_count": 0,
            "last_model_action": "",
            "last_fallback_action": "",
            "last_fallback_reason": "",
            "terminal_answer_valid": False,
            "terminal_answer_validation_reason": "",
            "decision": None,
            "plan": None,
            "tool_calls": [],
            "tool_results": [],
            "debug_events": [],
        }
        if continuation_mode == "expand_source":
            state["current_source_index"] = min(len(state["source_candidates"]), int(state["current_source_index"]) + 1)
            state["current_page_snapshot"] = {}
            state["current_page_candidate_links"] = []
            state["current_page_answer_context"] = {}
            state["current_page_url"] = ""
            state["current_page_type"] = ""
        return state

    def _plan_research_task(self, user_request: str, route_plan: dict[str, Any]) -> dict[str, Any]:
        validated_route_plan = self._validate_task_plan(route_plan)
        if validated_route_plan:
            return validated_route_plan
        try:
            response = self.llm.execute_task(
                _TASK_PLAN_PROMPT,
                json.dumps(
                    {
                        "user_request": str(user_request or "").strip(),
                        "route_plan": dict(route_plan or {}),
                    },
                    ensure_ascii=False,
                ),
                max_tokens=220,
                temperature=0.0,
                instruction_label="Web research task planning",
            )
        except Exception:
            logger.debug("web_research_task_plan_failed", exc_info=True)
            return {}
        return self._validate_task_plan(self._parse_action(str(getattr(response, "text", "") or "")))

    @staticmethod
    def _validate_task_plan(plan: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(plan, dict):
            return {}
        task_type = str(plan.get("task_type") or "").strip().lower()
        if task_type not in {"specs", "compare", "news", "release", "product_lookup", "general_info"}:
            return {}
        return {
            "task_type": task_type,
            "entity": str(plan.get("entity") or "").strip(),
            "search_query": str(plan.get("search_query") or "").strip(),
            "answer_focus": str(plan.get("answer_focus") or "").strip(),
            "reason": str(plan.get("reason") or "").strip(),
        }

    def _allowed_actions(self, state: dict[str, Any]) -> list[str]:
        if not state["source_candidates"] and state["discover_calls"] < self.MAX_DISCOVER_CALLS:
            return ["discover_sources", "fail"]
        if state["current_page_snapshot"]:
            actions = ["open_link", "answer", "fail"]
            if state["revise_count"] < self.MAX_REVISE_QUERY:
                actions.insert(1, "revise_query")
            return actions
        if state["source_candidates"] and state["page_open_count"] < self.MAX_PAGE_OPENS:
            actions = ["open_source", "fail"]
            if state["revise_count"] < self.MAX_REVISE_QUERY:
                actions.insert(1, "revise_query")
            return actions
        if state["revise_count"] < self.MAX_REVISE_QUERY:
            return ["revise_query", "fail"]
        return ["fail"]

    def _model_action(self, state: dict[str, Any], allowed: list[str]) -> dict[str, Any]:
        prompt = {
            "user_query": state["user_query"],
            "effective_query": state["effective_query"],
            "task_type": state["task_type"],
            "entity": state["entity"],
            "continuation_mode": state["continuation_mode"],
            "allowed_actions": allowed,
            "sources": [
                {
                    "index": index,
                    "title": str(item.get("title") or "").strip(),
                    "url": str(item.get("url") or "").strip(),
                    "domain": str(item.get("domain") or "").strip(),
                }
                for index, item in enumerate(list(state["source_candidates"])[:5])
            ],
            "current_source_index": state["current_source_index"],
            "attempted_source_indices": list(state["attempted_source_indices"]),
            "current_page": {
                "url": state["current_page_url"],
                "title": str((state["current_page_snapshot"] or {}).get("title") or "").strip(),
                "page_type_guess": state["current_page_type"],
                "headings": [str(item).strip() for item in list((state["current_page_snapshot"] or {}).get("headings") or [])[:6] if str(item).strip()],
                "visible_text_excerpt": str((state["current_page_snapshot"] or {}).get("visible_text") or "")[:1600],
                "serp_detected": bool((state["current_page_snapshot"] or {}).get("serp_detected")),
                "candidate_link_count": len(list(state["current_page_candidate_links"])),
                "answer_context": dict(state.get("current_page_answer_context") or {}),
                "blocked_for_answer": bool((state.get("current_page_answer_context") or {}).get("is_obviously_unanswerable")),
                "block_reason": str((state.get("current_page_answer_context") or {}).get("block_reason") or "").strip(),
            },
            "candidate_links": list(state["current_page_candidate_links"])[:8],
            "search_queries_attempted": list(state["search_queries_attempted"]),
            "provider_quality_state": state["provider_quality_state"],
            "terminal_answer_validation_reason": str(state.get("terminal_answer_validation_reason") or "").strip(),
        }
        sections = [_ACTION_PROMPT]
        prompt_context = getattr(self.prompt_context, "system_prompt", "") if self.prompt_context is not None else ""
        if prompt_context:
            sections.append(str(prompt_context).strip())
        if self.memory_context:
            sections.append(self.memory_context)
        try:
            response = self.llm.execute_task(
                "\n\n".join(section for section in sections if section),
                json.dumps(prompt, ensure_ascii=False),
                max_tokens=280,
                temperature=0.0,
                instruction_label="Web research tool loop",
            )
        except Exception:
            logger.debug("web_research_tool_loop_llm_failed", exc_info=True)
            return {}
        return self._parse_action(str(getattr(response, "text", "") or ""))

    @staticmethod
    def _parse_action(text: str) -> dict[str, Any]:
        text = str(text or "").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            return dict(json.loads(text[start : end + 1]) or {})
        except Exception:
            return {}

    def _valid_action(self, action: dict[str, Any], state: dict[str, Any], allowed: list[str]) -> bool:
        name = str(action.get("action") or "").strip()
        if name not in allowed:
            return False
        if name == "discover_sources":
            return bool(str(action.get("query") or state.get("revised_query") or state.get("effective_query") or "").strip())
        if name == "open_source":
            index = self._int_or_default(action.get("source_index"), -1)
            return 0 <= index < len(list(state["source_candidates"]))
        if name == "open_link":
            link_id = self._int_or_default(action.get("link_id"), -1)
            return any(self._int_or_default(item.get("id"), -1) == link_id for item in list(state["current_page_candidate_links"]))
        if name == "revise_query":
            return bool(str(action.get("revised_query") or "").strip() or self._revise_query(state))
        return True

    def _fallback_action(self, state: dict[str, Any], allowed: list[str]) -> dict[str, Any]:
        if "discover_sources" in allowed:
            action = {
                "action": "discover_sources",
                "query": str(state.get("revised_query") or state.get("effective_query") or "").strip(),
                "reason": "deterministic_discover",
            }
            self._record_debug_event(
                state,
                "fallback_action_chosen",
                chosen_action="discover_sources",
                reason="deterministic_discover",
                answer_context_blocked=False,
            )
            return action
        answer_context = dict(state.get("current_page_answer_context") or {})
        if (
            "answer" in allowed
            and answer_context
            and not bool(answer_context.get("is_obviously_unanswerable"))
            and not str(state.get("terminal_answer_validation_reason") or "").strip()
        ):
            action = {
                "action": "answer",
                "stop_detail": self._default_stop_detail(state),
                "citation_page_ids": self._citation_ids(state),
            }
            self._record_debug_event(
                state,
                "fallback_action_chosen",
                chosen_action="answer",
                reason="answer_context_ready",
                answer_context_blocked=False,
            )
            return action
        if "open_link" in allowed:
            for item in list(state["current_page_candidate_links"]):
                if str(item.get("url") or "").strip() not in set(state["visited_urls"]):
                    action = {
                        "action": "open_link",
                        "link_id": int(item.get("id") or 0),
                        "reason": "deterministic_top_link",
                    }
                    self._record_debug_event(
                        state,
                        "fallback_action_chosen",
                        chosen_action="open_link",
                        reason="deterministic_top_link",
                        answer_context_blocked=bool(answer_context.get("is_obviously_unanswerable")),
                    )
                    return action
        if "open_source" in allowed:
            attempted = {int(item) for item in list(state["attempted_source_indices"]) if str(item).strip()}
            for index, item in enumerate(list(state["source_candidates"])):
                if str(item.get("url") or "").strip() and index not in attempted:
                    action = {
                        "action": "open_source",
                        "source_index": index,
                        "reason": "deterministic_next_source",
                    }
                    self._record_debug_event(
                        state,
                        "fallback_action_chosen",
                        chosen_action="open_source",
                        reason="deterministic_next_source",
                        answer_context_blocked=bool(answer_context.get("is_obviously_unanswerable")),
                    )
                    return action
        if "revise_query" in allowed:
            action = {
                "action": "revise_query",
                "revised_query": self._revise_query(state),
                "reason": "deterministic_revise",
            }
            self._record_debug_event(
                state,
                "fallback_action_chosen",
                chosen_action="revise_query",
                reason="deterministic_revise",
                answer_context_blocked=bool(answer_context.get("is_obviously_unanswerable")),
            )
            return action
        action = {
            "action": "fail",
            "failure_reason": state.get("failure_reason") or "browse_navigation_exhausted",
            "reason": "deterministic_fail",
        }
        self._record_debug_event(
            state,
            "fallback_action_chosen",
            chosen_action="fail",
            reason="deterministic_fail",
            answer_context_blocked=bool(answer_context.get("is_obviously_unanswerable")),
        )
        return action

    def _step(self, state: dict[str, Any], action: dict[str, Any]) -> SkillResult | None:
        name = str(action.get("action") or "").strip()
        if name == "discover_sources":
            self._discover_sources(state, action)
            if state["source_candidates"]:
                return None
            if state["discover_calls"] >= self.MAX_DISCOVER_CALLS and state["revise_count"] >= self.MAX_REVISE_QUERY:
                return self._failure(
                    state,
                    "browse_source_discovery_failed",
                    "未能发现可用网页来源。",
                    stop_reason="no_sources",
                    stop_detail="browse_source_discovery_failed",
                )
            return None
        if name == "open_source":
            return self._open_source(state, action)
        if name == "open_link":
            return self._open_link(state, action)
        if name == "revise_query":
            state["revised_query"] = str(action.get("revised_query") or "").strip() or self._revise_query(state)
            state["revise_count"] += 1
            state["source_candidates"] = []
            state["current_page_snapshot"] = {}
            state["current_page_candidate_links"] = []
            state["current_page_answer_context"] = {}
            state["current_page_url"] = ""
            state["current_page_type"] = ""
            state["attempted_source_indices"] = []
            state["stop_reason"] = "revise_query"
            state["stop_detail"] = "query_revised"
            return None
        if name == "answer":
            result = self._answer(state, action)
            if result is not None:
                return result
            return None
        if name == "fail":
            return self._failure(
                state,
                str(action.get("failure_reason") or state.get("failure_reason") or "browse_navigation_exhausted").strip() or "browse_navigation_exhausted",
                str(action.get("reason") or "网页浏览已达到当前边界。").strip() or "网页浏览已达到当前边界。",
                stop_reason="fail",
                stop_detail="model_requested_fail",
            )
        return self._failure(
            state,
            "invalid_model_output_fallback_exhausted",
            "网页浏览决策失败。",
            stop_reason="fail",
            stop_detail="invalid_model_output_fallback_exhausted",
        )

    def _discover_sources(self, state: dict[str, Any], action: dict[str, Any]) -> None:
        query = str(action.get("query") or state.get("revised_query") or state.get("effective_query") or state.get("user_query") or "").strip()
        state["discover_calls"] += 1
        self._emit_progress("understanding_request", task_type=state["task_type"], query=query)
        result = self._call_tool(
            state,
            "discover_sources",
            {"query": query, "task_type": state["task_type"]},
            "source_discovery_completed",
            lambda: self.web.discover_sources(
                query=query,
                task_type=state["task_type"],
                context=self._tool_context(state),
                revised_query=str(state.get("revised_query") or "").strip(),
                search_again_used=bool(state.get("revised_query")),
            ),
        )
        browse_entry = dict(result.get("browse_entry") or {})
        gate = dict(result.get("quality_gate") or {})
        state["decision"] = result.get("decision")
        state["plan"] = result.get("plan")
        state["entity"] = str(result.get("entity") or state.get("entity") or "").strip()
        state["source_candidates"] = list(result.get("sources") or [])
        state["current_source_index"] = int(browse_entry.get("current_source_index") or 0)
        state["attempted_source_indices"] = list(browse_entry.get("attempted_source_indices") or [])
        state["provider_diagnostics"] = list(result.get("provider_diagnostics") or [])
        state["provider_quality_state"] = str(gate.get("provider_quality_state") or "").strip()
        state["provider_quality_gate_triggered"] = bool(gate.get("provider_quality_gate_triggered"))
        state["low_signal_fallback_to_serp_used"] = bool(gate.get("low_signal_fallback_to_serp_used"))
        state["search_queries_attempted"] = list(result.get("search_queries_attempted") or [])

    def _open_source(self, state: dict[str, Any], action: dict[str, Any]) -> SkillResult | None:
        index = self._int_or_default(action.get("source_index"), 0)
        sources = list(state["source_candidates"])
        if index < 0 or index >= len(sources):
            return None
        state["attempted_source_indices"] = sorted(
            {int(item) for item in list(state["attempted_source_indices"]) if str(item).strip()} | {index}
        )
        state["current_source_index"] = index
        return self._open_page(state, str(sources[index].get("url") or "").strip(), navigation_kind="open_source")

    def _open_link(self, state: dict[str, Any], action: dict[str, Any]) -> SkillResult | None:
        link_id = self._int_or_default(action.get("link_id"), -1)
        link = next(
            (item for item in list(state["current_page_candidate_links"]) if self._int_or_default(item.get("id"), -1) == link_id),
            None,
        )
        if link is None:
            return None
        url = str(link.get("url") or "").strip()
        if not url:
            return None
        state["selected_links"].append(
            {
                "url": url,
                "text": str(link.get("text") or "").strip(),
                "reason": str(action.get("reason") or "").strip(),
            }
        )
        return self._open_page(state, url, navigation_kind="open_link")

    def _open_page(self, state: dict[str, Any], url: str, *, navigation_kind: str) -> SkillResult | None:
        if state["page_open_count"] >= self.MAX_PAGE_OPENS:
            return self._failure(
                state,
                "browse_navigation_exhausted",
                "网页打开次数已达上限。",
                stop_reason="exploration_budget_exhausted",
                stop_detail="page_open_limit_reached",
            )
        if not url:
            return None
        domain = urlparse(url).netloc.lower()
        if navigation_kind == "open_link":
            same_domain = dict(state["same_domain_hops"])
            next_count = int(same_domain.get(domain) or 0) + 1
            if next_count > self.MAX_SAME_DOMAIN_HOPS:
                return None
            same_domain[domain] = next_count
            state["same_domain_hops"] = same_domain
            self._emit_progress("navigating_deeper", url=url, task_type=state["task_type"])
        else:
            self._emit_progress("opening_page", url=url, task_type=state["task_type"])
        result = self._call_tool(
            state,
            "open_page",
            {"url": url, "task_type": state["task_type"]},
            "understanding_page",
            lambda: self.web.open_page(
                url,
                task_type=state["task_type"],
                entity=state["entity"],
                decision=state.get("decision"),
                plan=state.get("plan"),
                max_candidate_links=8,
            ),
        )
        state["page_open_count"] += 1
        if str(result.get("status") or "").strip() != "ok":
            state["failure_reason"] = str(result.get("failure_reason") or "open_failed").strip() or "open_failed"
            return None
        snapshot = dict(result.get("snapshot") or {})
        state["current_page_snapshot"] = snapshot
        state["current_page_candidate_links"] = list(result.get("candidate_links") or [])
        state["current_page_url"] = str(result.get("final_url") or url).strip()
        state["current_page_type"] = str(result.get("page_type_guess") or "").strip()
        state["terminal_answer_valid"] = False
        state["terminal_answer_validation_reason"] = ""
        state["visited_urls"] = set(state["visited_urls"]) | {state["current_page_url"]}
        state["opened_pages"].append(
            {
                "page_id": len(list(state["opened_pages"])),
                "url": state["current_page_url"],
                "title": str(snapshot.get("title") or "").strip(),
                "page_type": state["current_page_type"],
            }
        )
        state["current_page_answer_context"] = self._build_page_answer_context(state)
        answer_context = dict(state["current_page_answer_context"] or {})
        self._record_debug_event(
            state,
            "page_answer_context_built",
            current_page_url=state["current_page_url"],
            page_type=str(state["current_page_type"] or "").strip(),
            source_type=str(state["current_page_type"] or "").strip(),
            answer_context_summary_present=bool(str(answer_context.get("task_relevant_summary") or "").strip()),
            evidence_count=len(list(answer_context.get("key_evidence") or [])),
            answer_signals=dict(answer_context.get("confidence_signals") or {}),
            blocked_for_answer=bool(answer_context.get("is_obviously_unanswerable")),
            block_reason=str(answer_context.get("block_reason") or "").strip(),
        )
        if bool(answer_context.get("is_obviously_unanswerable")):
            state["stop_reason"] = "blocked_page"
            state["stop_detail"] = str(answer_context.get("block_reason") or "").strip()
            self._record_debug_event(
                state,
                "answer_blocked",
                current_page_url=state["current_page_url"],
                blocked_for_answer=True,
                block_reason=str(answer_context.get("block_reason") or "").strip(),
            )
        return None

    def _answer(self, state: dict[str, Any], action: dict[str, Any]) -> SkillResult | None:
        self._emit_progress("extracting_answer", task_type=state["task_type"], url=state["current_page_url"])
        answer = self._generate_answer_text(state, seed_text=str(action.get("response_text") or "").strip())
        valid, validation_reason = self._is_valid_terminal_answer(state, answer)
        state["terminal_answer_valid"] = bool(valid)
        state["terminal_answer_validation_reason"] = str(validation_reason or "").strip()
        self._record_debug_event(
            state,
            "terminal_answer_validated",
            current_page_url=state["current_page_url"],
            terminal_answer_valid=bool(valid),
            terminal_answer_validation_reason=str(validation_reason or "").strip(),
        )
        if not valid:
            self._record_debug_event(
                state,
                "answer_validation_blocked",
                task_type=state["task_type"],
                current_page_url=state["current_page_url"],
                reason=state["terminal_answer_validation_reason"],
            )
            return None
        stop_detail = str(action.get("stop_detail") or action.get("stop_reason") or "").strip() or self._default_stop_detail(state)
        state["stop_reason"] = "answer_ready"
        state["stop_detail"] = stop_detail
        self._record_debug_event(
            state,
            "stop_decided",
            stop_reason="answer_ready",
            stop_detail=stop_detail,
        )
        summary = str(answer.get("summary") or "").strip() or "当前页面能确认到一些相关信息。"
        response_text = str(answer.get("response_text") or "").strip() or summary
        recommendation = str(answer.get("recommendation") or "").strip()
        structured = self._build_structured_payload(state, success=True)
        structured["summary"] = summary
        structured["recommendation"] = recommendation
        structured["answer_text"] = response_text
        if isinstance(answer.get("fields"), list):
            structured["fields"] = [
                {
                    "label": str(item.get("label") or "").strip(),
                    "value": str(item.get("value") or "").strip(),
                }
                for item in list(answer.get("fields") or [])
                if isinstance(item, dict) and str(item.get("label") or "").strip() and str(item.get("value") or "").strip()
            ][:6]
        card_type = ""
        if isinstance(answer.get("card"), dict):
            structured["card"] = dict(answer.get("card") or {})
            card_type = str((answer.get("card") or {}).get("type") or "").strip().lower()
            structured["card_type"] = card_type
        citation_ids = self._citation_ids(state, action.get("citation_page_ids"))
        structured["citation_page_ids"] = citation_ids
        sources = [
            {
                "title": str(item.get("title") or item.get("url") or "").strip(),
                "url": str(item.get("url") or "").strip(),
            }
            for item in list(state["opened_pages"])
            if str(item.get("url") or "").strip()
        ]
        structured["sources"] = sources
        return SkillResult(
            skill_name=BUNDLE_NAME,
            success=True,
            summary=summary,
            structured=structured,
            recommendation=recommendation,
            sources=sources,
            tool_results=list(state["tool_results"]),
            response_text=response_text,
            tool_lock=card_type in {"specs", "compare", "release", "web_brief", "news_list"},
        )

    def _failure(
        self,
        state: dict[str, Any],
        failure_reason: str,
        message: str,
        *,
        stop_reason: str = "",
        stop_detail: str = "",
    ) -> SkillResult:
        state["failure_reason"] = str(failure_reason or "").strip() or "browse_navigation_exhausted"
        state["stop_reason"] = str(stop_reason or state.get("stop_reason") or self._default_failure_stop_reason(failure_reason)).strip()
        state["stop_detail"] = str(stop_detail or state.get("stop_detail") or self._default_failure_stop_detail(failure_reason)).strip()
        self._record_debug_event(
            state,
            "stop_decided",
            stop_reason=state["stop_reason"],
            stop_detail=state["stop_detail"],
            failure_reason=state["failure_reason"],
        )
        structured = self._build_structured_payload(state, success=False)
        sources = [
            {
                "title": str(item.get("title") or item.get("url") or "").strip(),
                "url": str(item.get("url") or "").strip(),
            }
            for item in list(state["opened_pages"])
            if str(item.get("url") or "").strip()
        ]
        return SkillResult(
            skill_name=BUNDLE_NAME,
            success=False,
            summary=message,
            structured=structured,
            recommendation="如果你愿意，我可以继续换来源或者改写查询再找一轮。",
            sources=sources,
            tool_results=list(state["tool_results"]),
            response_text=message,
        )

    def _build_structured_payload(self, state: dict[str, Any], *, success: bool) -> dict[str, Any]:
        final_page = dict(state["current_page_snapshot"] or {})
        current_links = list(state["current_page_candidate_links"] or [])
        answer_context = dict(state.get("current_page_answer_context") or {})
        web_context = {
            "last_sources": list(state["source_candidates"]),
            "last_current_source_index": int(state["current_source_index"] or 0),
            "last_attempted_source_indices": list(state["attempted_source_indices"]),
            "last_links": current_links,
            "last_page": final_page,
            "last_page_answer_context": answer_context,
            "last_task_type": state["task_type"],
            "last_query": state["effective_query"],
            "last_search_again_used": bool(state["revised_query"]),
            "last_revised_query": str(state["revised_query"] or "").strip(),
            "last_final_page_url": str(state["current_page_url"] or "").strip(),
            "last_final_page_type": str(state["current_page_type"] or "").strip(),
            "last_stop_reason": str(state["stop_reason"] or "").strip(),
            "last_stop_detail": str(state.get("stop_detail") or "").strip(),
            "last_fallback_action": str(state.get("last_fallback_action") or "").strip(),
            "last_fallback_reason": str(state.get("last_fallback_reason") or "").strip(),
            "last_provider_quality_state": str(state["provider_quality_state"] or "").strip(),
            "last_provider_quality_gate_triggered": bool(state["provider_quality_gate_triggered"]),
            "last_low_signal_fallback_to_serp_used": bool(state["low_signal_fallback_to_serp_used"]),
            "last_search_queries_attempted": list(state["search_queries_attempted"]),
            "last_discover_calls": int(state["discover_calls"] or 0),
            "last_page_open_count": int(state["page_open_count"] or 0),
            "visited_urls": sorted(str(item).strip() for item in set(state["visited_urls"]) if str(item).strip()),
            "last_debug_events": list(state.get("debug_events") or []),
        }
        return {
            "bundle_name": BUNDLE_NAME,
            "forced_bundle": "web-research",
            "task_type": state["task_type"],
            "web_task_type": state["task_type"],
            "success": bool(success),
            "stop_reason": str(state["stop_reason"] or "").strip(),
            "stop_detail": str(state.get("stop_detail") or "").strip(),
            "failure_reason": str(state["failure_reason"] or "").strip(),
            "web_loop_action_count": int(state["web_loop_action_count"] or 0),
            "last_model_action": str(state["last_model_action"] or "").strip(),
            "last_fallback_action": str(state.get("last_fallback_action") or "").strip(),
            "last_fallback_reason": str(state.get("last_fallback_reason") or "").strip(),
            "tool_calls": list(state["tool_calls"]),
            "debug_events": list(state.get("debug_events") or []),
            "citation_page_ids": self._citation_ids(state),
            "search_queries_attempted": list(state["search_queries_attempted"]),
            "source_candidates": list(state["source_candidates"]),
            "selected_links": list(state["selected_links"]),
            "provider_diagnostics": list(state["provider_diagnostics"]),
            "provider_quality_state": str(state["provider_quality_state"] or "").strip(),
            "provider_quality_gate_triggered": bool(state["provider_quality_gate_triggered"]),
            "low_signal_fallback_to_serp_used": bool(state["low_signal_fallback_to_serp_used"]),
            "current_page_url": str(state["current_page_url"] or "").strip(),
            "current_page_type": str(state["current_page_type"] or "").strip(),
            "current_page_title": str(final_page.get("title") or "").strip(),
            "current_page_answer_context": answer_context,
            "answer_context_summary": str(answer_context.get("task_relevant_summary") or "").strip(),
            "answer_context_evidence": list(answer_context.get("key_evidence") or []),
            "answer_context_blocked": bool(answer_context.get("is_obviously_unanswerable")),
            "answer_context_block_reason": str(answer_context.get("block_reason") or "").strip(),
            "terminal_answer_valid": bool(state.get("terminal_answer_valid")),
            "terminal_answer_validation_reason": str(state.get("terminal_answer_validation_reason") or "").strip(),
            "web_context": web_context,
        }

    def _citation_ids(self, state: dict[str, Any], explicit: Any | None = None) -> list[int]:
        if isinstance(explicit, list):
            values: list[int] = []
            for item in explicit:
                try:
                    values.append(int(item))
                except Exception:
                    continue
            if values:
                return values
        opened_pages = list(state["opened_pages"])
        if not opened_pages:
            return []
        return [int(opened_pages[-1].get("page_id") or len(opened_pages) - 1)]

    def _generate_answer_text(self, state: dict[str, Any], *, seed_text: str) -> dict[str, str]:
        seed_summary = seed_text[:220].strip() if seed_text else ""
        if seed_text and self.services.llm_helper is None:
            return {
                "summary": seed_summary or "当前页面能确认到一些相关信息。",
                "recommendation": "",
                "response_text": seed_text,
            }
        answer_context = dict(state.get("current_page_answer_context") or {})
        page = {
            "title": str((state["current_page_snapshot"] or {}).get("title") or "").strip(),
            "url": str(state.get("current_page_url") or "").strip(),
            "body_text": "\n".join(
                item
                for item in [
                    str(answer_context.get("task_relevant_summary") or "").strip(),
                    *[str(entry).strip() for entry in list(answer_context.get("key_evidence") or []) if str(entry).strip()],
                ]
                if item
            )
            or str((state["current_page_snapshot"] or {}).get("visible_text") or "").strip(),
            "headings": list((state["current_page_snapshot"] or {}).get("headings") or []),
            "key_values": list((state["current_page_snapshot"] or {}).get("key_values") or []),
            "table_rows": list((state["current_page_snapshot"] or {}).get("table_rows") or []),
            "snapshot": dict(state["current_page_snapshot"] or {}),
            "structured_signals": {
                "domain": urlparse(str(state.get("current_page_url") or "").strip()).netloc.lower(),
                "page_type": str(state.get("current_page_type") or "").strip(),
            },
        }
        helper = self.services.llm_helper
        if helper is not None and hasattr(helper, "summarize_web_result"):
            try:
                result = helper.summarize_web_result(
                    state["user_query"],
                    [page],
                    {},
                    memory_context=self.memory_context,
                    task_type_hint=str(state.get("task_type") or "").strip(),
                    answer_focus=str((state.get("task_plan") or {}).get("answer_focus") or "").strip(),
                )
                if isinstance(result, dict) and str(result.get("response_text") or "").strip():
                    return {
                        "summary": str(result.get("summary") or seed_summary or "当前页面能确认到一些相关信息。").strip(),
                        "recommendation": str(result.get("recommendation") or "").strip(),
                        "response_text": str(result.get("response_text") or seed_text or "").strip(),
                        "fields": list(result.get("fields", [])) if isinstance(result.get("fields"), list) else [],
                        "card": dict(result.get("card") or {}) if isinstance(result.get("card"), dict) else {},
                    }
            except Exception:
                logger.debug("web_research_loop_summary_helper_failed", exc_info=True)
        if seed_text:
            return {
                "summary": seed_summary or "当前页面能确认到一些相关信息。",
                "recommendation": "",
                "response_text": seed_text,
            }
        prompt = {
            "user_query": state["user_query"],
            "task_type": state["task_type"],
            "current_page_url": state["current_page_url"],
            "title": page["title"],
            "headings": page["headings"][:8],
            "answer_context": answer_context,
            "visible_text_excerpt": page["body_text"][:1800],
        }
        try:
            response = self.llm.execute_task(
                _ANSWER_PROMPT,
                json.dumps(prompt, ensure_ascii=False),
                max_tokens=320,
                temperature=0.1,
                instruction_label="Web research answer synthesis",
            )
            parsed = self._parse_action(str(getattr(response, "text", "") or ""))
            if parsed:
                return {
                    "summary": str(parsed.get("summary") or "当前页面能确认到一些相关信息。").strip(),
                    "recommendation": str(parsed.get("recommendation") or "").strip(),
                    "response_text": str(parsed.get("response_text") or parsed.get("summary") or "").strip(),
                }
        except Exception:
            logger.debug("web_research_loop_answer_llm_failed", exc_info=True)
        title = page["title"] or page["url"]
        return {
            "summary": f"当前页面能确认到与“{title}”相关的信息。",
            "recommendation": "",
            "response_text": f"当前页面能确认到与“{title}”相关的信息，但还不足以整理成更完整的结论。",
        }

    def _build_page_answer_context(self, state: dict[str, Any]) -> dict[str, Any]:
        snapshot = dict(state["current_page_snapshot"] or {})
        if not snapshot:
            return {
                "page_url": str(state.get("current_page_url") or "").strip(),
                "page_title": "",
                "page_type": str(state.get("current_page_type") or "").strip(),
                "task_type": str(state.get("task_type") or "").strip(),
                "task_relevant_summary": "",
                "key_evidence": [],
                "confidence_signals": {"evidence_count": 0, "page_quality_score": -1},
                "is_obviously_unanswerable": True,
                "block_reason": "missing_page_snapshot",
            }
        task_type = str(state.get("task_type") or "").strip().lower()
        title = str(snapshot.get("title") or "").strip()
        page_type = str(state.get("current_page_type") or snapshot.get("page_type") or "").strip()
        visible_text = str(snapshot.get("visible_text") or "").strip()
        headings = [str(item).strip() for item in list(snapshot.get("headings") or []) if str(item).strip()]
        evidence = self._extract_key_evidence(task_type, title, headings, visible_text)
        summary = self._extract_task_relevant_summary(task_type, title, headings, evidence, visible_text)
        blocked, block_reason = self._should_block_answer(state, summary=summary, evidence=evidence)
        page_quality, quality_reason, low_quality = self._page_quality(state)
        signals = self._collect_answer_signals(
            state,
            evidence=evidence,
            summary=summary,
            page_quality=page_quality,
            quality_reason=quality_reason,
            low_quality=low_quality,
        )
        return {
            "page_url": str(state.get("current_page_url") or "").strip(),
            "page_title": title,
            "page_type": page_type,
            "task_type": task_type,
            "task_relevant_summary": summary,
            "key_evidence": evidence,
            "key_values": [str(item).strip() for item in list(snapshot.get("key_values") or []) if str(item).strip()][:16],
            "table_rows": [str(item).strip() for item in list(snapshot.get("table_rows") or []) if str(item).strip()][:16],
            "confidence_signals": signals,
            "is_obviously_unanswerable": blocked,
            "block_reason": block_reason,
        }

    def _extract_task_relevant_summary(
        self,
        task_type: str,
        title: str,
        headings: list[str],
        evidence: list[str],
        visible_text: str,
    ) -> str:
        if evidence:
            return " ".join(evidence[:3]).strip()
        fragments = [title.strip(), *[item.strip() for item in headings[:3]], str(visible_text or "").strip()[:320]]
        return " ".join(item for item in fragments if item).strip()

    def _extract_key_evidence(
        self,
        task_type: str,
        title: str,
        headings: list[str],
        visible_text: str,
    ) -> list[str]:
        text = str(visible_text or "").replace("\r", "\n")
        raw_lines = [str(item).strip() for item in text.split("\n")]
        lines: list[str] = []
        seen: set[str] = set()
        for item in [title, *headings, *raw_lines]:
            cleaned = re.sub(r"\s+", " ", str(item or "").strip())
            if len(cleaned) < 4:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            lines.append(cleaned)
        if task_type == "specs":
            scored = [
                line
                for line in lines
                if any(token in line.lower() for token in ("spec", "parameter", "config", "规格", "参数", "配置", "display", "battery", "range", "horsepower"))
                or bool(re.search(r"\d", line))
            ]
            return scored[:6]
        if task_type == "compare":
            scored = [
                line
                for line in lines
                if any(token in line.lower() for token in ("compare", "comparison", "vs", "versus", "区别", "差异", "对比"))
                or bool(re.search(r"\d", line))
            ]
            return scored[:6]
        if task_type in {"news", "release"}:
            scored = [
                line
                for line in lines
                if any(token in line.lower() for token in ("news", "press", "blog", "latest", "发布", "新闻"))
                or bool(re.search(r"\b20\d{2}\b|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4}", line))
            ]
            return scored[:6]
        scored = [
            line
            for line in lines
            if any(token in line.lower() for token in ("about", "overview", "what is", "company", "platform", "service", "介绍", "公司", "平台", "服务"))
            or len(line) >= 28
        ]
        return scored[:5]

    def _collect_answer_signals(
        self,
        state: dict[str, Any],
        *,
        evidence: list[str],
        summary: str,
        page_quality: int,
        quality_reason: str,
        low_quality: bool,
    ) -> dict[str, Any]:
        return {
            "evidence_count": len(list(evidence)),
            "summary_present": bool(str(summary or "").strip()),
            "page_quality_score": int(page_quality),
            "page_quality_reason": str(quality_reason or "").strip(),
            "low_quality": bool(low_quality),
            "candidate_link_count": len(list(state.get("current_page_candidate_links") or [])),
        }

    def _should_block_answer(self, state: dict[str, Any], *, summary: str = "", evidence: list[str] | None = None) -> tuple[bool, str]:
        snapshot = dict(state["current_page_snapshot"] or {})
        if not snapshot:
            return True, "missing_page_snapshot"
        if bool(snapshot.get("serp_detected")):
            return True, "serp_page"
        url = str(state.get("current_page_url") or snapshot.get("final_url") or snapshot.get("url") or "").strip().lower()
        title = str(snapshot.get("title") or "").strip().lower()
        visible_text = str(snapshot.get("visible_text") or "").strip()
        if not visible_text:
            return True, "missing_visible_text"
        if any(token in url or token in title for token in ("login", "signin", "sign-in", "redirect", "search?")):
            return True, "login_or_redirect_page"
        if any(token in url or token in title for token in ("support", "help")):
            return True, "support_page_blocked"
        task_type = str(state.get("task_type") or "").strip().lower()
        page_type = str(state.get("current_page_type") or snapshot.get("page_type") or "").strip().lower()
        if page_type == "homepage" and task_type in {"specs", "compare", "news", "release"}:
            return True, "homepage_not_sufficient_for_task"
        page_quality, _, low_quality = self._page_quality(state)
        evidence_list = [str(item).strip() for item in list(evidence or []) if str(item).strip()]
        if low_quality and not evidence_list and not str(summary or "").strip():
            return True, "low_quality_without_evidence"
        if page_quality < 0 and len(visible_text) < 120:
            return True, "low_signal_page"
        return False, ""

    def _is_valid_terminal_answer(self, state: dict[str, Any], answer_payload: dict[str, Any]) -> tuple[bool, str]:
        response_text = str(answer_payload.get("response_text") or answer_payload.get("summary") or "").strip()
        if not response_text:
            return False, "empty_response_text"
        answer_context = dict(state.get("current_page_answer_context") or {})
        if not answer_context:
            return False, "missing_answer_context"
        if bool(answer_context.get("is_obviously_unanswerable")):
            return False, str(answer_context.get("block_reason") or "page_blocked_for_answer").strip() or "page_blocked_for_answer"
        citation_ids = self._citation_ids(state)
        if not citation_ids and not str(state.get("current_page_url") or "").strip():
            return False, "missing_page_evidence"
        return True, ""

    def _page_quality(self, state: dict[str, Any]) -> tuple[int, str, bool]:
        snapshot = dict(state["current_page_snapshot"] or {})
        url = str(state.get("current_page_url") or snapshot.get("final_url") or snapshot.get("url") or "").strip().lower()
        title = str(snapshot.get("title") or "").strip().lower()
        visible_text = str(snapshot.get("visible_text") or "").strip().lower()
        page_type = str(state.get("current_page_type") or snapshot.get("page_type") or "").strip().lower()
        task_type = str(state.get("task_type") or "").strip().lower()
        entity = str(state.get("entity") or "").strip()
        score = 0
        reasons: list[str] = []
        if snapshot.get("serp_detected"):
            score -= 4
            reasons.append("serp_page")
        if any(term in url or term in title for term in _LOW_QUALITY_URL_TERMS):
            score -= 4
            reasons.append("low_quality_domain_or_path")
        if page_type in {"docs", "product", "news"}:
            score += 2
            reasons.append(f"page_type:{page_type}")
        if page_type == "homepage":
            if task_type in {"specs", "compare", "news", "release"}:
                score -= 1
                reasons.append("homepage_for_non_home_task")
            else:
                score += 1
                reasons.append("homepage_general_info")
        if len(visible_text) >= 240:
            score += 1
            reasons.append("has_body_text")
        if entity and self._entity_in_text(entity, " ".join((url, title, visible_text[:1200]))):
            score += 2
            reasons.append("entity_match")
        low_quality = score < 0
        return score, ",".join(reasons), low_quality

    def _default_stop_detail(self, state: dict[str, Any]) -> str:
        task_type = str(state.get("task_type") or "").strip().lower()
        if task_type == "general_info":
            return "general_info_page_reached"
        if task_type == "specs":
            return "specs_page_reached"
        if task_type == "compare":
            return "comparison_page_reached"
        if task_type in {"news", "release"}:
            return "news_page_reached"
        if task_type == "product_lookup":
            return "product_page_reached"
        return "useful_page_reached"

    @staticmethod
    def _default_failure_stop_reason(failure_reason: str) -> str:
        normalized = str(failure_reason or "").strip().lower()
        if normalized == "browse_source_discovery_failed":
            return "no_sources"
        if normalized == "browse_navigation_exhausted":
            return "exploration_budget_exhausted"
        if normalized == "browse_target_not_reached":
            return "low_signal"
        return "fail"

    @staticmethod
    def _default_failure_stop_detail(failure_reason: str) -> str:
        normalized = str(failure_reason or "").strip().lower()
        if normalized == "browse_navigation_exhausted":
            return "exploration_budget_exhausted"
        return normalized or "failure"

    def _revise_query(self, state: dict[str, Any]) -> str:
        planned_query = str(((state.get("task_plan") or {}).get("search_query") or "")).strip()
        if planned_query:
            return planned_query
        entity = str(state.get("entity") or "").strip()
        effective_query = str(state.get("effective_query") or state.get("user_query") or "").strip()
        task_type = str(state.get("task_type") or "").strip().lower()
        if task_type == "general_info":
            return f"{entity or effective_query} about".strip()
        if task_type == "specs":
            return f"{entity or effective_query} specs".strip()
        if task_type == "compare":
            return f"{effective_query} comparison".strip()
        if task_type in {"news", "release"}:
            return f"{entity or effective_query} latest news".strip()
        if task_type == "product_lookup":
            return f"{entity or effective_query} official product".strip()
        return effective_query

    def _tool_context(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "web_context": self._build_structured_payload(state, success=True).get("web_context", {}),
            "current_page_url": str(state.get("current_page_url") or "").strip(),
            "task_type": str(state.get("task_type") or "").strip(),
        }

    def _emit_progress(self, phase: str, **payload: Any) -> None:
        self.services.emit("structured_tool_progress", {"phase": phase, **payload})

    def _record_debug_event(self, state: dict[str, Any], phase: str, **payload: Any) -> None:
        event_payload = {"phase": phase, **payload}
        state["debug_events"].append(event_payload)
        if phase == "fallback_action_chosen":
            state["last_fallback_action"] = str(payload.get("chosen_action") or "").strip()
            state["last_fallback_reason"] = str(payload.get("reason") or "").strip()
        self._emit_progress(phase, **payload)

    def _call_tool(
        self,
        state: dict[str, Any],
        tool_name: str,
        kwargs: dict[str, Any],
        success_phase: str,
        fn: Any,
    ) -> dict[str, Any]:
        permission_payload = {"bundle": BUNDLE_NAME, "tool_name": tool_name, "policy": "allow", "kwargs": kwargs}
        self.services.emit("permission_request", permission_payload)
        self.services.emit("pre_tool_use", permission_payload)
        self.services.emit("tool_call_start", {"tool_name": tool_name, "kwargs": kwargs})
        try:
            result = dict(fn() or {})
            self.services.emit("tool_call_done", {"tool_name": tool_name, "result": result})
            self.services.emit("post_tool_use", {"bundle": BUNDLE_NAME, "tool_name": tool_name, "status": "done"})
            self._emit_progress(success_phase, tool_name=tool_name, **kwargs)
            self._record_tool_call(state, tool_name, kwargs, result=result, ok=True)
            return result
        except Exception as exc:
            error = str(exc) or "tool_failed"
            self.services.emit("tool_call_failed", {"tool_name": tool_name, "error": error, "kwargs": kwargs})
            self.services.emit("post_tool_use", {"bundle": BUNDLE_NAME, "tool_name": tool_name, "status": "failed", "error": error})
            self._record_tool_call(state, tool_name, kwargs, result={}, ok=False, error=error)
            raise

    def _record_tool_call(
        self,
        state: dict[str, Any],
        tool_name: str,
        kwargs: dict[str, Any],
        *,
        result: dict[str, Any],
        ok: bool,
        error: str = "",
    ) -> None:
        compact_result = result
        if tool_name == "open_page":
            compact_result = {
                "status": str(result.get("status") or "").strip(),
                "final_url": str(result.get("final_url") or "").strip(),
                "page_type_guess": str(result.get("page_type_guess") or "").strip(),
                "candidate_link_count": len(list(result.get("candidate_links") or [])),
            }
        elif tool_name == "discover_sources":
            compact_result = {
                "source_count": len(list(result.get("sources") or [])),
                "chosen_entry_url": str(result.get("chosen_entry_url") or "").strip(),
                "provider_quality_state": str((result.get("quality_gate") or {}).get("provider_quality_state") or "").strip(),
            }
        state["tool_calls"].append(
            {
                "tool_name": tool_name,
                "ok": bool(ok),
                "kwargs": dict(kwargs),
                "result": compact_result,
                "error": error,
            }
        )
        state["tool_results"].append(
            ToolResult(
                tool_name=tool_name,
                ok=bool(ok),
                data=dict(compact_result or {}),
                error=error,
            )
        )

    def _continuation_mode(self, user_request: str) -> str:
        lowered = str(user_request or "").strip().lower()
        if any(marker in lowered for marker in _EXPAND_SOURCE_MARKERS):
            return "expand_source"
        if any(marker in lowered for marker in _CONTINUE_MARKERS):
            return "continue"
        return "none"

    def _infer_task_type(self, user_request: str) -> str:
        lowered = f" {str(user_request or '').strip().lower()} "
        for task_type, markers in _TASK_MARKERS.items():
            if any(marker.lower() in lowered for marker in markers):
                return task_type
        return "general_info"

    def _infer_entity(self, query: str, task_type: str) -> str:
        lowered = str(query or "").strip().lower()
        if not lowered:
            return ""
        for markers in _TASK_MARKERS.values():
            for marker in markers:
                lowered = lowered.replace(marker.lower(), " ")
        lowered = lowered.replace("官网", " ").replace("今天", " ").replace("最近", " ")
        lowered = re.sub(r"\s+", " ", lowered).strip(" ?？。.!！")
        if task_type == "compare":
            lowered = lowered.replace("和", " ").replace("与", " ").replace("vs", " ")
        return lowered

    @staticmethod
    def _entity_in_text(entity: str, text: str) -> bool:
        compact_entity = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(entity or "").lower())
        compact_text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(text or "").lower())
        if not compact_entity or not compact_text:
            return False
        if compact_entity in compact_text:
            return True
        compact_no_digits = re.sub(r"\d+", "", compact_entity)
        return bool(compact_no_digits and compact_no_digits in compact_text)

    @staticmethod
    def _int_or_default(value: Any, default: int) -> int:
        try:
            if value is None or value == "":
                return default
            return int(value)
        except Exception:
            return default
