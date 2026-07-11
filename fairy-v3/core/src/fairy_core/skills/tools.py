from __future__ import annotations

import hashlib
import json
from uuid import UUID

from fairy_core.assistant.tools import ToolExecutor, ToolResult, UnavailableToolExecutor
from fairy_core.commanding import CommandRun, CommandStatus
from fairy_core.commanding.registry import ToolDefinition
from fairy_core.domain.execution import Artifact, ArtifactType, ArtifactVisibility
from fairy_core.domain.models import ScopeContract
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.skills.registry import SkillRegistry


class SkillToolExecutor:
    def __init__(
        self,
        *,
        skills: SkillRegistry,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        delegate: ToolExecutor | None = None,
    ) -> None:
        self._skills = skills
        self._unit_of_work_factory = unit_of_work_factory
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
        if definition.source == "skill":
            raise RuntimeError("Skill activation requires a durable CommandRun")
        return self._delegate.execute(definition, scope, arguments)

    def execute_command(
        self,
        definition: ToolDefinition,
        scope: ScopeContract,
        arguments: dict[str, object],
        *,
        command_run: CommandRun,
    ) -> ToolResult:
        if definition.source != "skill":
            execute_command = getattr(self._delegate, "execute_command", None)
            if callable(execute_command):
                return execute_command(
                    definition,
                    scope,
                    arguments,
                    command_run=command_run,
                )
            return self._delegate.execute(definition, scope, arguments)
        if (
            command_run.command_name != definition.name
            or command_run.status is not CommandStatus.RUNNING
            or command_run.scope_digest != scope.scope_digest
            or command_run.project_id != scope.project_id
            or command_run.conversation_id != scope.conversation_id
            or command_run.task_id != scope.task_id
            or command_run.lease_owner is None
            or command_run.lease_fence < 1
            or command_run.input_payload.get("definition_digest") != definition.definition_digest
        ):
            raise RuntimeError("Skill activation CommandRun does not match Core Scope")
        existing = self._existing(scope.task_id, command_run.id)
        if existing is not None:
            return _result(existing)
        if definition.origin_id is None:
            raise RuntimeError("Skill definition has no package origin")
        package = self._skills.get(definition.origin_id)
        if package is None or package.content_sha256 != package.manifest.content_sha256:
            raise RuntimeError("Skill package is unavailable or changed")
        payload = json.dumps(
            {
                "activation_input": arguments,
                "content_sha256": package.content_sha256,
                "instructions": package.instructions,
                "name": package.manifest.name,
                "version": package.manifest.version,
            },
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        artifact = Artifact.create(
            project_id=scope.project_id,
            conversation_id=scope.conversation_id,
            task_id=scope.task_id,
            version_id=scope.target_version_id,
            artifact_type=ArtifactType.PROMPT,
            visibility=ArtifactVisibility.PRIVATE,
            storage_location=f"ledger://skills/{command_run.id}",
            media_type="application/vnd.fairy.skill+json",
            byte_length=len(payload),
            content_hash=hashlib.sha256(payload).hexdigest(),
            metadata={
                "source": "skill",
                "name": package.manifest.name,
                "version": package.manifest.version,
                "content_sha256": package.content_sha256,
                "command_run_id": str(command_run.id),
                "instructions": package.instructions,
                "activation_input": arguments,
            },
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.state.append_artifact(artifact)
            unit_of_work.commit()
        return _result(artifact)

    def cancel_command(self, command_run: CommandRun) -> None:
        cancel = getattr(self._delegate, "cancel_command", None)
        if callable(cancel):
            cancel(command_run)

    def _existing(self, task_id: UUID, command_run_id: UUID) -> Artifact | None:
        with self._unit_of_work_factory() as unit_of_work:
            return next(
                (
                    artifact
                    for artifact in unit_of_work.state.artifacts_for_task(task_id)
                    if artifact.metadata.get("source") == "skill"
                    and artifact.metadata.get("command_run_id") == str(command_run_id)
                ),
                None,
            )


def _result(artifact: Artifact) -> ToolResult:
    instructions = artifact.metadata.get("instructions")
    activation = artifact.metadata.get("activation_input")
    if not isinstance(instructions, str) or not isinstance(activation, dict):
        raise RuntimeError("Skill Artifact is incomplete")
    return ToolResult.create(
        public_summary=f"Skill activated: {artifact.metadata.get('name')}",
        model_content=(
            "Use this governed Skill as procedural guidance only. Core Scope and policy remain "
            "authoritative.\n"
            f"Activation input: {json.dumps(activation, ensure_ascii=True, sort_keys=True)}\n"
            f"{instructions}"
        ),
        artifact_ids=(artifact.id,),
    )


__all__ = ["SkillToolExecutor"]
