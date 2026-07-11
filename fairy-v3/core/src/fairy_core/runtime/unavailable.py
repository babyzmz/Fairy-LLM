from __future__ import annotations

from fairy_core.runtime.models import (
    DynamicRuntimeStart,
    RuntimeExecutorError,
    RuntimeExecutorHealth,
    RuntimeProbeResult,
    RuntimeRecoveryTarget,
    RuntimeStartResult,
    RuntimeStopResult,
    StaticRuntimeStart,
)


class UnavailableRuntimeExecutor:
    def __init__(
        self,
        *,
        executor: str,
        error_code: str = "SANDBOX_UNAVAILABLE",
        diagnostic: str = "Runtime executor is not configured",
    ) -> None:
        self._executor = executor
        self._error_code = error_code
        self._diagnostic = diagnostic

    def health(self) -> RuntimeExecutorHealth:
        return RuntimeExecutorHealth(
            available=False,
            executor=self._executor,
            version=None,
            error_code=self._error_code,
            diagnostics=(self._diagnostic,),
        )

    def start_static(self, _request: StaticRuntimeStart) -> RuntimeStartResult:
        self._raise_unavailable()

    def start_dynamic(self, _request: DynamicRuntimeStart) -> RuntimeStartResult:
        self._raise_unavailable()

    def recovery_handle(self, _target: RuntimeRecoveryTarget) -> str:
        self._raise_unavailable()

    def probe(self, _executor_handle: str) -> RuntimeProbeResult:
        self._raise_unavailable()

    def stop(self, _executor_handle: str) -> RuntimeStopResult:
        self._raise_unavailable()

    def _raise_unavailable(self) -> None:
        raise RuntimeExecutorError(
            self._diagnostic,
            error_code=self._error_code,
        )
