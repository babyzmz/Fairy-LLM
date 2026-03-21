"""TerminalAgent stub — future shell/code execution runtime.

TODO: Implement full terminal agent.
Currently pure-LLM via terminal-agent skill bundle.

When ready, migrate:
  app/skills/bundles/agent_shell/ runtime logic → here
And register in app/core/capability_registry.py.
"""

from __future__ import annotations

from app.agents.terminal_agent.contract import TerminalRequest, TerminalResult


class TerminalAgent:
    """Stub: terminal/shell execution agent (not yet implemented)."""

    def execute(self, request: TerminalRequest) -> TerminalResult:
        raise NotImplementedError(
            "TerminalAgent is not yet implemented. "
            "Route to terminal-agent skill bundle for now."
        )
