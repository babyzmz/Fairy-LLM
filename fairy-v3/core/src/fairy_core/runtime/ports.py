from __future__ import annotations

from typing import Protocol

from fairy_core.runtime.models import (
    RuntimeExecutorHealth,
    RuntimeProbeResult,
    RuntimeStartResult,
    RuntimeStopResult,
    StaticRuntimeStart,
)


class RuntimeExecutor(Protocol):
    def health(self) -> RuntimeExecutorHealth: ...

    def start_static(self, request: StaticRuntimeStart) -> RuntimeStartResult: ...

    def probe(self, executor_handle: str) -> RuntimeProbeResult: ...

    def stop(self, executor_handle: str) -> RuntimeStopResult: ...
