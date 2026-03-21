"""Invocation Service — dispatches skill execution to agent backends.

Bridge between the LLM skill layer and the agent execution layer.

Usage::

    svc = InvocationService(search_tool=search_web, fetch_page=read_webpage)
    result = svc.invoke("realtime_lookup", query="比特币价格")
    # result: RealtimeLookupResult-compatible dict
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.core.capability_registry import CapabilityRegistry, get_registry

logger = logging.getLogger(__name__)


class InvocationService:
    """Dispatches skill invocations to the appropriate agent backend.

    Pipeline (per request)::

        resolved = state_mgr.resolve_query(session_id, message)
        result   = svc.invoke(skill, resolved, session_id=session_id, ...)
        # InvocationService calls state_mgr.update_after_agent internally.
    """

    def __init__(
        self,
        search_tool: Callable[..., list[dict]] | None = None,
        fetch_page: Callable[[str], str] | None = None,
        vision_fallback: Callable[[str], Any] | None = None,
        registry: CapabilityRegistry | None = None,
        state_manager: Any | None = None,
    ) -> None:
        self._search_tool = search_tool
        self._fetch_page = fetch_page
        self._vision_fallback = vision_fallback
        self._registry = registry or get_registry()
        self._agent_cache: dict[str, Any] = {}
        # Lazy import avoids circular deps; can be injected for testing
        self._state_manager = state_manager

    def invoke(
        self,
        skill_name: str,
        query: str,
        allowed_tools: list[str] | None = None,
        strict_mode: bool = True,
        request_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        """Invoke an agent for the given skill and query.

        If *session_id* is provided, the resolved_query is passed to the agent
        and state is updated after a successful invocation.

        Returns a normalized result dict. Never raises.
        """
        cap = self._registry.get(skill_name)
        if cap is None:
            return self._fail(f"Unknown skill: {skill_name}", "skill_not_registered")
        if cap.is_pure_llm:
            return self._fail("Pure-LLM skill — no agent backend", "pure_llm_skill")

        agent = self._get_agent(skill_name)
        if agent is None:
            return self._fail(f"Agent load failed: {skill_name}", "agent_load_error")

        # --- conversation state: resolve query before dispatch ---
        resolved_query = query
        if session_id:
            mgr = self._get_state_manager()
            if mgr is not None:
                resolved_query = mgr.resolve_query(session_id, query)
                if resolved_query != query:
                    logger.info(
                        "invocation_service reference_resolved original=%r resolved=%r",
                        query[:40], resolved_query[:60],
                    )

        logger.info("invocation_service skill=%s query=%s", skill_name, resolved_query[:50])
        try:
            if skill_name in ("web_research", "web-research"):
                from app.agents.web_research.models import WebResearchRequest
                req = WebResearchRequest(
                    query=resolved_query,
                    session_id=session_id,
                    request_id=request_id,
                    allowed_tools=allowed_tools or ["search_web", "fetch_page"],
                    strict_mode=strict_mode,
                )
                result = agent.run(req)
            else:
                from app.agents.realtime_lookup.models import RealtimeLookupRequest
                req = RealtimeLookupRequest(
                    query=resolved_query,
                    allowed_tools=allowed_tools or ["search_web"],
                    strict_mode=strict_mode,
                    request_id=request_id,
                )
                result = agent.execute(req)
            result_dict = result.to_dict()
        except Exception as exc:
            logger.exception("invocation_error skill=%s error=%s", skill_name, exc)
            return self._fail(str(exc), "execution_error")

        # --- conversation state: update after successful invocation ---
        if session_id and result_dict.get("success"):
            mgr = self._get_state_manager()
            if mgr is not None:
                try:
                    agent_name = type(agent).__name__
                    mgr.update_after_agent(
                        session_id,
                        query=query,
                        resolved_query=resolved_query,
                        agent_result=result_dict,
                        agent_name=agent_name,
                    )
                except Exception as exc:
                    logger.warning("state_update_error %s", exc)

        return result_dict

    def _get_state_manager(self) -> Any | None:
        """Lazy-load the conversation state manager to avoid circular imports."""
        if self._state_manager is not None:
            return self._state_manager
        try:
            from app.core.conversation_state import get_manager
            self._state_manager = get_manager()
            return self._state_manager
        except Exception as exc:
            logger.debug("state_manager_unavailable %s", exc)
            return None

    def _get_agent(self, skill_name: str) -> Any:
        if skill_name in self._agent_cache:
            return self._agent_cache[skill_name]
        AgentClass = self._registry.load_agent_class(skill_name)
        if AgentClass is None:
            return None
        try:
            agent = AgentClass(
                search_tool=self._search_tool,
                fetch_page=self._fetch_page,
                vision_fallback=self._vision_fallback,
            )
            self._agent_cache[skill_name] = agent
            return agent
        except Exception as exc:
            logger.error("agent_init_error skill=%s error=%s", skill_name, exc)
            return None

    @staticmethod
    def _fail(msg: str, reason: str) -> dict[str, Any]:
        return {
            "success": False, "answer": msg, "card": None,
            "source": "", "source_urls": [], "confidence": 0.0,
            "tool_lock": False, "tool_lock_valid": False,
            "needs_clarification": False, "reason": reason,
        }
