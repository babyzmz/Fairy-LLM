from __future__ import annotations

from fairy_core.assistant.tools import ToolExecutor, ToolResult, UnavailableToolExecutor
from fairy_core.commanding import CommandRun
from fairy_core.commanding.registry import ToolDefinition
from fairy_core.domain.models import ScopeContract
from fairy_core.media.application import MediaApplication, MediaGenerationResult
from fairy_core.providers import CancellationToken


class MediaToolExecutor:
    def __init__(
        self,
        *,
        application: MediaApplication,
        delegate: ToolExecutor | None = None,
    ) -> None:
        self._application = application
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
        return self._delegate.execute(definition, scope, arguments)

    def execute_command(
        self,
        definition: ToolDefinition,
        scope: ScopeContract,
        arguments: dict[str, object],
        *,
        command_run: CommandRun,
    ) -> ToolResult:
        return self.execute_command_with_cancellation(
            definition,
            scope,
            arguments,
            command_run=command_run,
            cancellation=CancellationToken(),
        )

    def execute_command_with_cancellation(
        self,
        definition: ToolDefinition,
        scope: ScopeContract,
        arguments: dict[str, object],
        *,
        command_run: CommandRun,
        cancellation: CancellationToken,
    ) -> ToolResult:
        if not definition.name.startswith("media."):
            execute = getattr(self._delegate, "execute_command_with_cancellation", None)
            if callable(execute):
                return execute(
                    definition,
                    scope,
                    arguments,
                    command_run=command_run,
                    cancellation=cancellation,
                )
            execute_command = getattr(self._delegate, "execute_command", None)
            if callable(execute_command):
                return execute_command(
                    definition,
                    scope,
                    arguments,
                    command_run=command_run,
                )
            return self._delegate.execute(definition, scope, arguments)
        if definition.name == "media.images.generate":
            result = self._application.generate_image(
                task_id=scope.task_id,
                command_run=command_run,
                prompt=_argument_text(arguments, "prompt"),
                output_path=_optional_argument_text(arguments, "output_path"),
                size=_optional_argument_text(arguments, "size") or "1024x1024",
                aspect_ratio=_optional_argument_text(arguments, "aspect_ratio") or "1:1",
                seed=_optional_argument_int(arguments, "seed"),
                idempotency_key=f"media-job:{command_run.id}",
                cancellation=cancellation,
            )
            return _tool_result(result)
        if definition.name == "media.audio.generate":
            result = self._application.generate_music(
                task_id=scope.task_id,
                command_run=command_run,
                prompt=_argument_text(arguments, "prompt"),
                output_path=_optional_argument_text(arguments, "output_path"),
                output_format=_optional_argument_text(arguments, "output_format") or "wav",
                seed=_optional_argument_int(arguments, "seed"),
                idempotency_key=f"media-job:{command_run.id}",
                cancellation=cancellation,
            )
            return _tool_result(result)
        if definition.name == "media.videos.start":
            result = self._application.start_video(
                task_id=scope.task_id,
                command_run=command_run,
                prompt=_argument_text(arguments, "prompt"),
                output_path=_optional_argument_text(arguments, "output_path"),
                duration_seconds=_optional_argument_int(arguments, "duration_seconds") or 5,
                resolution=_optional_argument_text(arguments, "resolution") or "720p",
                aspect_ratio=_optional_argument_text(arguments, "aspect_ratio") or "16:9",
                generate_audio=_optional_argument_bool(arguments, "generate_audio", default=True),
                seed=_optional_argument_int(arguments, "seed"),
                idempotency_key=f"media-job:{command_run.id}",
                cancellation=cancellation,
            )
            return _tool_result(result)
        raise RuntimeError("non-model media command cannot be executed by the Assistant")


def _tool_result(result: MediaGenerationResult) -> ToolResult:
    if result.artifact is not None:
        return ToolResult.create(
            public_summary=f"Generated {result.job.kind.value} at {result.job.output_path}",
            model_content=(f"Generated Artifact {result.artifact.id} at {result.job.output_path}."),
            artifact_ids=(result.artifact.id,),
        )
    return ToolResult.create(
        public_summary=f"Started video generation ({result.job.progress}%)",
        model_content=(
            f"Video generation job {result.job.id} is {result.job.status.value}. "
            "The result will appear in the Workspace when processing completes."
        ),
    )


def _argument_text(arguments: dict[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise ValueError(f"media argument {name} must be text")
    return value


def _optional_argument_text(arguments: dict[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"media argument {name} must be text")
    return value


def _optional_argument_int(arguments: dict[str, object], name: str) -> int | None:
    value = arguments.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"media argument {name} must be an integer")
    return value


def _optional_argument_bool(
    arguments: dict[str, object],
    name: str,
    *,
    default: bool,
) -> bool:
    value = arguments.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"media argument {name} must be a boolean")
    return value


__all__ = ["MediaToolExecutor"]
