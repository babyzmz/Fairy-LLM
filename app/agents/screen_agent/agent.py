"""ScreenAgent stub — future screen understanding runtime.

TODO: Implement full screen understanding agent.
Currently pure-LLM via screen-understanding skill bundle.

When ready, migrate:
  app/skills/bundles/screen_understanding/ runtime logic → here
And register in app/core/capability_registry.py.
"""

from __future__ import annotations

from app.agents.screen_agent.contract import ScreenRequest, ScreenResult


class ScreenAgent:
    """Stub: screen understanding agent (not yet implemented)."""

    def execute(self, request: ScreenRequest) -> ScreenResult:
        raise NotImplementedError(
            "ScreenAgent is not yet implemented. "
            "Route to screen-understanding skill bundle for now."
        )
