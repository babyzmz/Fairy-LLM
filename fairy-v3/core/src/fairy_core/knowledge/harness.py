from __future__ import annotations

import hashlib
import json

from fairy_core.commanding.registry import ToolRegistry
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.domain.models import ScopeContract, Task
from fairy_core.knowledge.models import (
    HarnessContextManifest,
    KnowledgeSnapshot,
    KnowledgeSnapshotItem,
    KnowledgeSnapshotStatus,
    KnowledgeSourceStatus,
)
from fairy_core.knowledge.repository import knowledge_request_fingerprint
from fairy_core.knowledge.tool_snapshot import ToolDefinitionSnapshot
from fairy_core.model_catalog.models import ModelSelectionSnapshot
from fairy_core.persistence.unit_of_work import CoreUnitOfWork
from fairy_core.persona import load_default_persona_authority


class KnowledgeSnapshotBuilder:
    def build(self, unit_of_work: CoreUnitOfWork, *, task: Task) -> KnowledgeSnapshot:
        revisions = (
            unit_of_work.knowledge.current_revisions(task.project_id)
            if task.project_id is not None
            else ()
        )
        sources = (
            unit_of_work.knowledge.list_sources(task.project_id)
            if task.project_id is not None
            else ()
        )
        degraded = tuple(
            source
            for source in sources
            if source.status
            in {
                KnowledgeSourceStatus.PARTIAL,
                KnowledgeSourceStatus.FAILED,
            }
        )
        items = tuple(
            KnowledgeSnapshotItem(
                ordinal=ordinal,
                item_id=revision.item_id,
                revision_id=revision.id,
                source_id=revision.source_id,
                relative_path=revision.relative_path,
                title=revision.title,
                content_hash=revision.content_hash,
                revision_hash=revision.revision_hash,
            )
            for ordinal, revision in enumerate(revisions)
        )
        snapshot = KnowledgeSnapshot.create(
            project_id=task.project_id,
            conversation_id=task.conversation_id,
            task_id=task.id,
            source_cursor=max((source.sync_cursor for source in sources), default=0),
            items=items,
            status=(
                KnowledgeSnapshotStatus.DEGRADED if degraded else KnowledgeSnapshotStatus.READY
            ),
            degraded_reason=("KNOWLEDGE_SOURCE_PARTIAL" if degraded else None),
        )
        return unit_of_work.knowledge.append_snapshot(
            snapshot,
            request_fingerprint=knowledge_request_fingerprint(task.id),
        )


class HarnessManifestBuilder:
    def __init__(
        self,
        registry: ToolRegistry,
        execution_policy: ExecutionPolicyResolver | None = None,
    ) -> None:
        self._registry = registry
        self._execution_policy = execution_policy or ExecutionPolicyResolver()

    def build(
        self,
        unit_of_work: CoreUnitOfWork,
        *,
        task: Task,
        scope: ScopeContract,
        knowledge_snapshot: KnowledgeSnapshot,
        profile_id: str,
        model_selection: ModelSelectionSnapshot | None,
    ) -> HarnessContextManifest:
        if task.workspace_id is None:
            raise ValueError("Task Workspace is required for a Harness Manifest")
        if task.memory_snapshot_id is None or task.memory_snapshot_hash is None:
            raise ValueError("Task Memory Snapshot is required for a Harness Manifest")
        policy = self._execution_policy.resolve(
            unit_of_work.execution_settings,
            execution_target=scope.execution_target,
        )
        definitions = tuple(
            sorted(
                self._registry.available_agent_definitions(
                    profile=policy.profile,
                    sandbox_healthy=policy.sandbox_healthy,
                    overrides=dict(policy.capability_overrides),
                ),
                key=lambda item: item.name,
            )
        )
        registry_digest = hashlib.sha256(
            json.dumps(
                [
                    {"name": definition.name, "digest": definition.definition_digest}
                    for definition in definitions
                ],
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        ).hexdigest()
        skills = tuple(
            sorted(
                f"{definition.origin_id}:{definition.definition_digest}"
                for definition in definitions
                if definition.source == "skill" and definition.origin_id is not None
            )
        )
        mcp = tuple(
            sorted(
                f"{definition.origin_id}:{definition.definition_digest}"
                for definition in definitions
                if definition.source == "mcp" and definition.origin_id is not None
            )
        )
        selection = (
            {
                "mode": model_selection.mode.value,
                "model_id": model_selection.model_id,
                "allow_free_fallback": model_selection.allow_free_fallback,
                "zero_data_retention": model_selection.zero_data_retention,
                "revision": model_selection.revision,
                "captured_at": model_selection.captured_at.isoformat(),
            }
            if model_selection is not None
            else {"mode": "legacy_profile", "profile_id": profile_id}
        )
        persona = load_default_persona_authority()
        manifest = HarnessContextManifest.create(
            task_id=task.id,
            scope_digest=scope.scope_digest,
            workspace_id=task.workspace_id,
            workspace_version_id=task.target_version_id or task.base_version_id,
            memory_snapshot_id=task.memory_snapshot_id,
            memory_snapshot_hash=task.memory_snapshot_hash,
            knowledge_snapshot_id=knowledge_snapshot.id,
            knowledge_snapshot_hash=knowledge_snapshot.content_hash,
            tool_registry_generation=self._registry.generation,
            tool_registry_digest=registry_digest,
            tool_definitions=tuple(ToolDefinitionSnapshot.capture(item) for item in definitions),
            skill_package_digests=skills,
            mcp_capability_snapshot=mcp,
            model_selection=selection,
            budget={
                "max_model_calls": 12,
                "max_tool_calls": 32,
                "max_repairs": 3,
                "max_duration_seconds": 1800,
            },
            persona_version=persona.version,
            persona_digest=persona.digest,
            persona_instruction=persona.system_prompt,
        )
        return unit_of_work.knowledge.append_manifest(manifest)


__all__ = ["HarnessManifestBuilder", "KnowledgeSnapshotBuilder"]
