"""
Fairy Tool Exposure Broker
==========================

Enforces the strict tool-visibility rule:

    Only tools listed in the selected skill's ``tools.json``
    are visible to the model.  All other tools MUST NOT be
    included in the prompt.

The broker sits between the skill bundle and the LLM call, acting as a
security and token-efficiency gate.

It works with the existing ToolRegistry (name→handler) by filtering which
tools are actually passed to the model for each request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class ExposureReport:
    """Diagnostic report for a single tool-exposure decision."""

    skill_name: str
    allowed_tools: list[str]
    exposed_tools: list[str]
    missing_tools: list[str]
    blocked_calls: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Broker
# ---------------------------------------------------------------------------


class ToolExposureBroker:
    """Central gatekeeper for tool visibility and validation.

    This broker works with the existing ToolRegistry by maintaining a parallel
    awareness of which tools exist and filtering them per-skill.
    """

    def __init__(self) -> None:
        self._known_tools: set[str] = set()
        self._active_allowlist: set[str] = set()
        self._active_skill: str = ""
        self._blocked_log: list[str] = []
        self._tool_registry: Any | None = None  # stored for get_tool() lookups

    # ------------------------------------------------------------------
    # Registry awareness
    # ------------------------------------------------------------------

    def register_known_tool(self, name: str) -> None:
        """Register a tool name so the broker knows it exists."""
        self._known_tools.add(name)

    def register_known_tools(self, names: list[str]) -> None:
        """Bulk-register tool names."""
        self._known_tools.update(names)

    def sync_from_tool_registry(self, tool_registry: Any) -> None:
        """Sync known tools from the existing ToolRegistry instance.

        Accepts any object with a ``list_tools() -> list[str]`` method.
        Also stores the registry reference so ``get_tool()`` can retrieve callables.
        """
        self._tool_registry = tool_registry
        try:
            tool_names = tool_registry.list_tools()
            self._known_tools.update(tool_names)
            logger.info("broker_synced_tools count=%d", len(tool_names))
        except Exception:
            logger.exception("broker_sync_failed")

    def get_tool(self, name: str) -> Any | None:
        """Return the callable for *name* from the underlying tool registry.

        Tries the following lookup strategies in order:
          1. ``tool_registry.get(name)``
          2. ``tool_registry.get_tool(name)``
          3. ``getattr(tool_registry, name, None)``

        Returns ``None`` if the tool is not found or no registry is stored.
        """
        if self._tool_registry is None:
            return None
        for method in ("get", "get_tool"):
            fn = getattr(self._tool_registry, method, None)
            if callable(fn):
                try:
                    result = fn(name)
                    if result is not None:
                        return result
                except Exception:
                    pass
        # Last resort: direct attribute
        return getattr(self._tool_registry, name, None)

    # ------------------------------------------------------------------
    # Exposure control
    # ------------------------------------------------------------------

    def activate_skill(self, skill_name: str, allowed_tools: list[str]) -> ExposureReport:
        """Set the active skill and compute the exposed tool set.

        Returns an ``ExposureReport`` for observability.
        """
        self._active_skill = skill_name
        self._active_allowlist = set(allowed_tools)
        self._blocked_log.clear()

        exposed = [t for t in allowed_tools if t in self._known_tools]
        missing = [t for t in allowed_tools if t not in self._known_tools]

        if missing:
            logger.warning(
                "tool_exposure_missing skill=%s missing=%s",
                skill_name,
                missing,
            )

        report = ExposureReport(
            skill_name=skill_name,
            allowed_tools=allowed_tools,
            exposed_tools=exposed,
            missing_tools=missing,
        )

        logger.info(
            "tool_exposure_activated skill=%s allowed=%d exposed=%d missing=%d exposed_list=%s",
            skill_name,
            len(allowed_tools),
            len(exposed),
            len(missing),
            ",".join(exposed),
        )
        return report

    def deactivate(self) -> None:
        """Clear the active skill and allowlist."""
        self._active_skill = ""
        self._active_allowlist.clear()
        self._blocked_log.clear()

    def get_exposed_tool_names(self) -> list[str]:
        """Return the list of tool names currently allowed for this skill."""
        if not self._active_allowlist:
            return []
        return [name for name in self._active_allowlist if name in self._known_tools]

    # ------------------------------------------------------------------
    # Runtime validation
    # ------------------------------------------------------------------

    def validate_tool_call(self, tool_name: str) -> bool:
        """Check whether a tool call from the model is permitted.

        Returns ``True`` if allowed, ``False`` if blocked.
        Blocked calls are logged for the current session.
        """
        if not self._active_allowlist:
            # No skill active → allow everything (fallback mode)
            return True

        if tool_name in self._active_allowlist:
            return True

        self._blocked_log.append(tool_name)
        logger.warning(
            "tool_call_blocked skill=%s tool=%s",
            self._active_skill,
            tool_name,
        )
        return False

    def get_blocked_calls(self) -> list[str]:
        """Return the list of tool calls that were blocked in this session."""
        return list(self._blocked_log)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_exposure_summary(self) -> dict[str, Any]:
        """Return a summary dict suitable for logging or metrics."""
        return {
            "active_skill": self._active_skill,
            "allowed_count": len(self._active_allowlist),
            "known_tools_count": len(self._known_tools),
            "blocked_count": len(self._blocked_log),
            "blocked_tools": list(self._blocked_log),
            "exposed_tools": self.get_exposed_tool_names(),
        }
