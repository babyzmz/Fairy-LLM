from __future__ import annotations

from threading import RLock

from fairy_core.commanding.registry import (
    ApprovalPolicy,
    RiskLevel,
    SideEffect,
    ToolDefinition,
    ToolRegistry,
)
from fairy_core.commanding.types import PermissionProfile
from fairy_core.skills.models import SkillPackage


class SkillRegistry:
    def __init__(self, tools: ToolRegistry) -> None:
        self._tools = tools
        self._packages: dict[str, SkillPackage] = {}
        self._enabled: set[str] = set()
        self._lock = RLock()

    def install(self, package: SkillPackage, *, enabled: bool = True) -> None:
        name = package.manifest.name
        with self._lock:
            if name in self._packages:
                raise ValueError(f"Skill is already installed: {name}")
            self._validate_capabilities(package)
            self._packages[name] = package
            if enabled:
                self._enabled.add(name)
            try:
                self._sync_tools()
            except Exception:
                self._enabled.discard(name)
                self._packages.pop(name, None)
                raise

    def replace(self, package: SkillPackage, *, enabled: bool | None = None) -> None:
        name = package.manifest.name
        with self._lock:
            previous = self._packages.get(name)
            previous_enabled = name in self._enabled
            if previous is None:
                raise KeyError(f"Skill is not installed: {name}")
            self._validate_capabilities(package)
            self._packages[name] = package
            if enabled is not None:
                if enabled:
                    self._enabled.add(name)
                else:
                    self._enabled.discard(name)
            try:
                self._sync_tools()
            except Exception:
                self._packages[name] = previous
                if previous_enabled:
                    self._enabled.add(name)
                else:
                    self._enabled.discard(name)
                self._sync_tools()
                raise

    def remove(self, name: str) -> SkillPackage:
        with self._lock:
            package = self._packages.pop(name, None)
            if package is None:
                raise KeyError(f"Skill is not installed: {name}")
            self._enabled.discard(name)
            self._sync_tools()
            return package

    def set_enabled(self, name: str, *, enabled: bool) -> None:
        with self._lock:
            package = self._packages.get(name)
            if package is None:
                raise KeyError(f"Skill is not installed: {name}")
            if enabled:
                self._validate_capabilities(package)
                self._enabled.add(name)
            else:
                self._enabled.discard(name)
            self._sync_tools()

    def enabled(self, name: str) -> bool:
        with self._lock:
            return name in self._enabled

    def get(self, name: str) -> SkillPackage | None:
        with self._lock:
            return self._packages.get(name)

    def packages(self) -> tuple[SkillPackage, ...]:
        with self._lock:
            return tuple(self._packages.values())

    def _validate_capabilities(self, package: SkillPackage) -> None:
        missing = sorted(
            operation
            for operation in package.manifest.required_capabilities
            if self._tools.get(operation) is None
        )
        if missing:
            raise ValueError(f"Skill requires unknown capabilities: {', '.join(missing)}")

    def _sync_tools(self) -> None:
        definitions = []
        for name in sorted(self._enabled):
            package = self._packages[name]
            definitions.append(
                ToolDefinition(
                    name=package.manifest.tool_name,
                    side_effect=SideEffect.READ,
                    risk_level=RiskLevel.LOW,
                    approval_policy=ApprovalPolicy.NEVER,
                    profiles=frozenset(PermissionProfile),
                    executor="skill_instructions",
                    idempotent=True,
                    model_visible=True,
                    description=(
                        f"Load the governed {package.manifest.name} Skill instructions. "
                        f"{package.manifest.description}"
                    ),
                    source="skill",
                    origin_id=name,
                    required_operations=frozenset(package.manifest.required_capabilities),
                    required_extensions=frozenset(
                        f"mcp:{server_id}"
                        for server_id in package.manifest.compatible_mcp_servers
                    ),
                    input_schema=package.manifest.input_schema,
                )
            )
        self._tools.replace_namespace("skill.", definitions)


__all__ = ["SkillRegistry"]
