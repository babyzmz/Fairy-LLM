from __future__ import annotations

from typing import Protocol

from fairy_core.runtime.models import (
    DynamicRuntimeStart,
    RuntimeExecutorHealth,
    RuntimeProbeResult,
    RuntimeRecoveryTarget,
    RuntimeStartResult,
    RuntimeStopResult,
    StaticRuntimeStart,
)


class RuntimeExecutor(Protocol):
    def health(self) -> RuntimeExecutorHealth: ...

    def start_static(self, request: StaticRuntimeStart) -> RuntimeStartResult: ...

    def start_dynamic(self, request: DynamicRuntimeStart) -> RuntimeStartResult: ...

    def recovery_handle(self, target: RuntimeRecoveryTarget) -> str: ...

    def probe(self, executor_handle: str) -> RuntimeProbeResult: ...

    def stop(self, executor_handle: str) -> RuntimeStopResult: ...
