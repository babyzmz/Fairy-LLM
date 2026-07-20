from __future__ import annotations

import json
from collections.abc import Mapping

from fairy_core.assistant.tools import ToolExecutor, ToolResult, UnavailableToolExecutor
from fairy_core.commanding.models import CommandRun, CommandStatus
from fairy_core.commanding.registry import ToolDefinition
from fairy_core.commanding.settings import SandboxHealthProvider
from fairy_core.domain.errors import ScopeViolationError
from fairy_core.domain.models import ScopeContract
from fairy_core.sandbox.archive import WorkspaceArchiveBuilder
from fairy_core.sandbox.models import (
    SandboxNetworkPolicy,
    SandboxRequest,
    SandboxResult,
    SandboxResultStatus,
)
from fairy_core.sandbox.ports import SandboxExecutor
from fairy_core.sandbox.wsl import SandboxUnavailableError

_TOOL_NAME = "run.sandboxed"
_EXECUTOR = "wsl_fairy_sandbox"
_EXECUTOR_VERSION = "1.0.0"


class SandboxCommandFailedError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        model_detail: str,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.model_detail = model_detail


class ExecutorSandboxHealthProvider(SandboxHealthProvider):
    def __init__(
        self,
        executor: SandboxExecutor,
        *,
        execution_target: str,
        expected_executor: str = _EXECUTOR,
        expected_version: str = _EXECUTOR_VERSION,
    ) -> None:
        self._executor = executor
        self._execution_target = execution_target
        self._expected_executor = expected_executor
        self._expected_version = expected_version

    def is_healthy(self, execution_target: str) -> bool:
        if execution_target != self._execution_target:
            return False
        health = self._executor.health()
        return (
            health.available
            and health.executor == self._expected_executor
            and health.version == self._expected_version
        )


class SandboxToolExecutor:
    def __init__(
        self,
        *,
        executor: SandboxExecutor,
        archive_builder: WorkspaceArchiveBuilder,
        execution_target: str,
        delegate: ToolExecutor | None = None,
    ) -> None:
        self._executor = executor
        self._archive_builder = archive_builder
        self._execution_target = execution_target
        self._delegate = delegate or UnavailableToolExecutor()

    def close(self) -> None:
        close = getattr(self._delegate, "close", None)
        if callable(close):
            close()

    def execute(
        self,
        definition: ToolDefinition,
        scope: ScopeContract,
        arguments: dict[str, object],
    ) -> ToolResult:
        if definition.name == _TOOL_NAME:
            raise SandboxUnavailableError(
                "SANDBOX_UNAVAILABLE: Sandbox execution requires a durable CommandRun"
            )
        return self._delegate.execute(definition, scope, arguments)

    def execute_command(
        self,
        definition: ToolDefinition,
        scope: ScopeContract,
        arguments: dict[str, object],
        *,
        command_run: CommandRun,
    ) -> ToolResult:
        if definition.name != _TOOL_NAME:
            delegated = getattr(self._delegate, "execute_command", None)
            if callable(delegated):
                return delegated(
                    definition,
                    scope,
                    arguments,
                    command_run=command_run,
                )
            return self._delegate.execute(definition, scope, arguments)
        self._validate_command_binding(definition, scope, command_run)
        if scope.execution_target != self._execution_target:
            raise SandboxUnavailableError(
                "SANDBOX_UNAVAILABLE: Sandbox executor does not match execution target"
            )
        argv = _arguments(arguments)
        if _starts_runtime_server(argv):
            raise SandboxCommandFailedError(
                "Long-running servers must be started by the Core Preview runtime",
                error_code="CAPABILITY_NOT_AVAILABLE",
                model_detail=(
                    "run.sandboxed only accepts commands that terminate. Do not start a server, "
                    "watcher, or development runtime and do not claim a host, port, or URL. "
                    "After the complete Changeset is applied, return the final response so Core "
                    "can start and verify the bound Preview."
                ),
            )
        archive = self._archive_builder.build(scope)
        request = SandboxRequest.create(
            job_id=command_run.id,
            project_id=scope.project_id,
            conversation_id=scope.conversation_id,
            task_id=scope.task_id,
            version_id=scope.target_version_id,
            scope_digest=scope.scope_digest,
            workspace_generation=archive.generation,
            lease_fence=command_run.lease_fence,
            argv=argv,
            cwd=_optional_string(arguments, "cwd", default="."),
            environment=_environment(arguments),
            timeout_seconds=_optional_integer(
                arguments,
                "timeout_seconds",
                default=120,
            ),
            output_limit_bytes=_optional_integer(
                arguments,
                "output_limit_bytes",
                default=262_144,
            ),
            network_policy=_network_policy(scope),
            workspace_archive=archive.content,
        )
        result = self._executor.execute(request)
        self._validate_result_binding(result, request)
        if result.status in {
            SandboxResultStatus.FAILED,
            SandboxResultStatus.TIMED_OUT,
        }:
            raise SandboxCommandFailedError(
                f"Sandbox command {result.status.value}",
                error_code=(
                    "WORKER_INTERRUPTED"
                    if result.status is SandboxResultStatus.TIMED_OUT
                    else "COMMAND_FAILED"
                ),
                model_detail=_failure_model_detail(result),
            )
        return _tool_result(result)

    def cancel_command(self, command_run: CommandRun) -> None:
        if command_run.command_name == _TOOL_NAME:
            if command_run.status is CommandStatus.RUNNING:
                self._executor.cancel(command_run.id)
            return
        delegated = getattr(self._delegate, "cancel_command", None)
        if callable(delegated):
            delegated(command_run)

    @staticmethod
    def _validate_command_binding(
        definition: ToolDefinition,
        scope: ScopeContract,
        command_run: CommandRun,
    ) -> None:
        if (
            command_run.command_name != definition.name
            or command_run.status is not CommandStatus.RUNNING
            or command_run.project_id != scope.project_id
            or command_run.conversation_id != scope.conversation_id
            or command_run.task_id != scope.task_id
            or command_run.scope_digest != scope.scope_digest
            or command_run.lease_fence < 1
        ):
            raise ScopeViolationError(
                "Sandbox CommandRun does not match Core Scope",
                code="SCOPE_MISMATCH",
            )

    @staticmethod
    def _validate_result_binding(
        result: SandboxResult,
        request: SandboxRequest,
    ) -> None:
        if (
            result.job_id != request.job_id
            or result.scope_digest != request.scope_digest
            or result.workspace_generation != request.workspace_generation
            or result.lease_fence != request.lease_fence
        ):
            raise ScopeViolationError(
                "Sandbox result does not match its immutable request",
                code="SCOPE_MISMATCH",
            )


def _arguments(values: Mapping[str, object]) -> tuple[str, ...]:
    raw = values.get("argv")
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError("argv must be an array of strings")
    return tuple(raw)


def _starts_runtime_server(argv: tuple[str, ...]) -> bool:
    lowered = tuple(value.casefold() for value in argv)
    executable = lowered[0].replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    executable = executable.removesuffix(".exe").removesuffix(".cmd")
    if any(value in {"--watch", "--watch-all", "watch"} for value in lowered[1:]):
        return True
    if executable in {"vite", "uvicorn", "gunicorn", "flask", "http-server", "serve"}:
        return True
    if executable in {"python", "python3", "py"}:
        modules = {
            lowered[index + 1]
            for index, value in enumerate(lowered[:-1])
            if value == "-m"
        }
        if modules & {"http.server", "uvicorn", "gunicorn", "flask"}:
            return True
    if executable in {"npm", "pnpm", "yarn", "bun"}:
        commands = {"dev", "serve", "start", "preview", "watch"}
        if any(value in commands for value in lowered[1:3]):
            return True
    if executable in {"npx", "pnpx", "bunx"} and len(lowered) > 1:
        return lowered[1] in {
            "vite",
            "next",
            "nuxt",
            "astro",
            "http-server",
            "serve",
            "uvicorn",
        }
    return False


def _failure_model_detail(result: SandboxResult) -> str:
    output = result.stderr or result.stdout
    detail = output.decode("utf-8", errors="replace").strip()
    if detail:
        return f"The terminating command failed: {detail[:1_500]}"
    if result.status is SandboxResultStatus.TIMED_OUT:
        return (
            "The command exceeded its timeout. Do not retry it as a server or watcher; use a "
            "bounded validation command and let Core own Preview startup."
        )
    return "The terminating command failed without diagnostic output."


def _environment(values: Mapping[str, object]) -> dict[str, str]:
    raw = values.get("environment", [])
    if not isinstance(raw, list):
        raise ValueError("environment must be an array")
    result: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("environment entries must be objects")
        name = item.get("name")
        value = item.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError("environment entries require string name and value")
        if name in result:
            raise ValueError(f"duplicate environment entry: {name}")
        result[name] = value
    return result


def _optional_string(
    values: Mapping[str, object],
    key: str,
    *,
    default: str,
) -> str:
    value = values.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    return value


def _optional_integer(
    values: Mapping[str, object],
    key: str,
    *,
    default: int,
) -> int:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _network_policy(scope: ScopeContract) -> SandboxNetworkPolicy:
    if scope.project_id is None and scope.network_policy == "open_web_safe":
        return SandboxNetworkPolicy.PUBLIC
    return SandboxNetworkPolicy.NONE


def _tool_result(result: SandboxResult) -> ToolResult:
    payload = {
        "job_id": str(result.job_id),
        "status": result.status.value,
        "exit_code": result.exit_code,
        "stdout": result.stdout.decode("utf-8", errors="replace"),
        "stderr": result.stderr.decode("utf-8", errors="replace"),
        "stdout_sha256": result.stdout_sha256,
        "stderr_sha256": result.stderr_sha256,
        "output_truncated": result.output_truncated,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
    }
    return ToolResult.create(
        public_summary=f"Sandbox command {result.status.value}",
        model_content=json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        artifact_ids=(),
    )


__all__ = ["ExecutorSandboxHealthProvider", "SandboxToolExecutor"]
