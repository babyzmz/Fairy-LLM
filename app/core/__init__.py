"""Core orchestration layer — inside the app package.

Owns:
  capability_registry  — maps skill names to agent classes
  invocation_service   — dispatches skill → agent
  skill_router         — routes intent → skill
  planner              — (stub) multi-step intent decomposition
  execution_context    — per-request execution metadata
"""
