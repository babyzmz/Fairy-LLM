"""Agents package — executable runtime orchestrators.

Layered architecture:
  skills/   = declarative LLM capability definitions
  agents/   = executable runtime systems  ← this package
  tools/    = low-level adapters/integrations
  core/     = planner, router, registry, dispatch

Available agents:
  realtime_lookup  — 5-stage autonomous realtime data lookup
  screen_agent     — screen understanding (stub, future)
  terminal_agent   — terminal / shell execution (stub, future)
"""

from app.agents.realtime_lookup.agent import RealtimeLookupAgent

__all__ = ["RealtimeLookupAgent"]
