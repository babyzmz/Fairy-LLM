from __future__ import annotations

from typing import Protocol
from uuid import UUID

from fairy_core.runtime.models import RuntimeExecutorHealth
from fairy_core.sandbox.models import SandboxRequest, SandboxResult


class SandboxExecutor(Protocol):
    def health(self) -> RuntimeExecutorHealth: ...

    def execute(self, request: SandboxRequest) -> SandboxResult: ...

    def cancel(self, job_id: UUID) -> None: ...


__all__ = ["SandboxExecutor"]
