"""Compatibility shim over CapabilityRegistry for agent-backend lookups."""

from __future__ import annotations

from app.core.capability_registry import CapabilityRegistry, get_registry


def _aliases(skill_name: str) -> list[str]:
    names = {skill_name}
    if "_" in skill_name:
        names.add(skill_name.replace("_", "-"))
    if "-" in skill_name:
        names.add(skill_name.replace("-", "_"))
    return sorted(names)


class SkillAgentRouter:
    """Thin adapter that delegates skill metadata lookups to CapabilityRegistry."""

    def __init__(self, registry: CapabilityRegistry | None = None) -> None:
        self._registry = registry or get_registry()

    def has_agent_backend(self, skill_name: str) -> bool:
        return self._registry.has_agent_backend(skill_name)

    def is_pure_llm(self, skill_name: str) -> bool:
        cap = self._registry.get(skill_name)
        return cap is not None and cap.is_pure_llm

    def get_agent_module(self, skill_name: str) -> str | None:
        cap = self._registry.get(skill_name)
        if cap is None or cap.is_pure_llm:
            return None
        return cap.agent_module or None

    def list_agent_backed_skills(self) -> list[str]:
        names: list[str] = []
        for cap in self._registry.list_all():
            if cap.is_pure_llm:
                continue
            names.extend(_aliases(cap.skill_name))
        return sorted(set(names))
