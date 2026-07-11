from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Protocol

from fairy_core.commanding.types import PermissionProfile


@dataclass(frozen=True, slots=True)
class ExecutionSettings:
    profile: PermissionProfile
    capability_overrides: Mapping[str, bool]
    revision: int
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("execution settings revision cannot be negative")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("execution settings updated_at must be timezone-aware")
        normalized: dict[str, bool] = {}
        for name, enabled in self.capability_overrides.items():
            canonical = name.strip()
            if not canonical or canonical != name or not isinstance(enabled, bool):
                raise ValueError("capability overrides must contain canonical names and booleans")
            normalized[canonical] = enabled
        object.__setattr__(
            self,
            "capability_overrides",
            MappingProxyType(dict(sorted(normalized.items()))),
        )
        object.__setattr__(self, "updated_at", self.updated_at.astimezone(UTC))

    @classmethod
    def defaults(cls, *, now: datetime | None = None) -> ExecutionSettings:
        return cls(
            profile=PermissionProfile.STANDARD,
            capability_overrides={},
            revision=0,
            updated_at=(now or datetime.now(UTC)),
        )


class ExecutionSettingsRepository(Protocol):
    def get(self) -> ExecutionSettings: ...

    def update(
        self,
        *,
        profile: PermissionProfile,
        capability_overrides: Mapping[str, bool],
        expected_revision: int,
        idempotency_key: str,
    ) -> ExecutionSettings: ...


class SandboxHealthProvider(Protocol):
    def is_healthy(self, execution_target: str) -> bool: ...


class StaticSandboxHealthProvider:
    def __init__(self, health: Mapping[str, bool] | None = None) -> None:
        self._health = MappingProxyType(dict(health or {}))

    def is_healthy(self, execution_target: str) -> bool:
        return self._health.get(execution_target, False) is True


@dataclass(frozen=True, slots=True)
class EffectiveExecutionPolicy:
    profile: PermissionProfile
    capability_overrides: Mapping[str, bool]
    sandbox_healthy: bool


class ExecutionPolicyResolver:
    def __init__(self, sandbox_health: SandboxHealthProvider | None = None) -> None:
        self._sandbox_health = sandbox_health or StaticSandboxHealthProvider()

    def resolve(
        self,
        repository: ExecutionSettingsRepository,
        *,
        execution_target: str,
    ) -> EffectiveExecutionPolicy:
        settings = repository.get()
        try:
            sandbox_healthy = self._sandbox_health.is_healthy(execution_target)
        except Exception:
            sandbox_healthy = False
        return EffectiveExecutionPolicy(
            profile=settings.profile,
            capability_overrides=settings.capability_overrides,
            sandbox_healthy=sandbox_healthy,
        )


__all__ = [
    "EffectiveExecutionPolicy",
    "ExecutionPolicyResolver",
    "ExecutionSettings",
    "ExecutionSettingsRepository",
    "SandboxHealthProvider",
    "StaticSandboxHealthProvider",
]
