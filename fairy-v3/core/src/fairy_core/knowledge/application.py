from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID

from fairy_core.contracts.knowledge import (
    HarnessContextManifestModel,
    HarnessManifestGetInput,
    KnowledgeCollectionModel,
    KnowledgeCollectionPageModel,
    KnowledgeGraphEdgeModel,
    KnowledgeGraphModel,
    KnowledgeGraphNodeModel,
    KnowledgeItemListInput,
    KnowledgeItemModel,
    KnowledgeItemPageModel,
    KnowledgeLinkPageModel,
    KnowledgeNodeKind,
    KnowledgeProjectInput,
    KnowledgeRelationKind,
    KnowledgeRevisionModel,
    KnowledgeRevisionPageModel,
    KnowledgeRevisionReadInput,
    KnowledgeSearchInput,
    KnowledgeSnapshotGetInput,
    KnowledgeSnapshotModel,
    KnowledgeSourceModel,
    KnowledgeSourcePageModel,
    ProjectKnowledgeOverviewModel,
)
from fairy_core.domain.execution import Artifact
from fairy_core.domain.models import Conversation, Project, Task
from fairy_core.knowledge.models import (
    KnowledgeRevision,
    KnowledgeSnapshot,
    KnowledgeSource,
    KnowledgeSourceStatus,
)
from fairy_core.memory.models import MemoryClaim, MemoryNamespace
from fairy_core.persistence.unit_of_work import CoreUnitOfWork, CoreUnitOfWorkFactory
from fairy_core.workspace.models import ProjectFile, ProjectIndex


@dataclass(frozen=True, slots=True)
class _ProjectContext:
    project: Project
    index: ProjectIndex | None
    conversations: tuple[Conversation, ...]
    tasks: tuple[Task, ...]
    artifacts: tuple[Artifact, ...]
    revisions: tuple[KnowledgeRevision, ...]
    sources: tuple[KnowledgeSource, ...]
    claims: tuple[MemoryClaim, ...]
    watermark: str
    source_revision: int


class ProjectKnowledgeApplication:
    def __init__(self, unit_of_work_factory: CoreUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def overview(self, request: KnowledgeProjectInput) -> ProjectKnowledgeOverviewModel:
        context = self._context(request.project_id)
        file_items = self._file_items(context.project, context.index)
        revision_items = self._revision_items(context.revisions)
        graph = self._graph(context)
        source_statuses = {source.status for source in context.sources}
        return ProjectKnowledgeOverviewModel(
            project_id=context.project.id,
            source_version_id=context.project.active_version_id,
            source_revision=context.source_revision,
            watermark=context.watermark,
            file_count=len(file_items),
            note_count=sum(
                item.kind in {KnowledgeNodeKind.NOTE, KnowledgeNodeKind.OBSIDIAN}
                for item in (*file_items, *revision_items)
            ),
            conversation_count=len(context.conversations),
            relation_count=len(graph.edges),
            obsidian_connected=bool(context.sources),
            obsidian_health=self._source_health(source_statuses),
        )

    def list_items(self, request: KnowledgeItemListInput) -> KnowledgeItemPageModel:
        context = self._context(request.project_id)
        items = (
            *self._file_items(context.project, context.index),
            *self._revision_items(context.revisions),
        )
        query = " ".join((request.query or "").casefold().split())
        if query:
            items = tuple(
                item
                for item in items
                if query
                in f"{item.title} {item.relative_path or ''} {item.language or ''}".casefold()
            )
        return KnowledgeItemPageModel(
            items=items[: request.limit],
            source_revision=context.source_revision,
            watermark=context.watermark,
        )

    def graph(self, request: KnowledgeProjectInput) -> KnowledgeGraphModel:
        return self._graph(self._context(request.project_id))

    def list_sources(self, request: KnowledgeProjectInput) -> KnowledgeSourcePageModel:
        with self._unit_of_work_factory() as unit_of_work:
            self._require_project(unit_of_work, request.project_id)
            sources = unit_of_work.knowledge.list_sources(request.project_id)
        return KnowledgeSourcePageModel(
            items=tuple(KnowledgeSourceModel.model_validate(source) for source in sources)
        )

    def list_collections(
        self,
        request: KnowledgeProjectInput,
    ) -> KnowledgeCollectionPageModel:
        with self._unit_of_work_factory() as unit_of_work:
            self._require_project(unit_of_work, request.project_id)
            collections = unit_of_work.knowledge.list_collections(request.project_id)
        return KnowledgeCollectionPageModel(
            items=tuple(
                KnowledgeCollectionModel.model_validate(collection) for collection in collections
            )
        )

    def get_snapshot(self, request: KnowledgeSnapshotGetInput) -> KnowledgeSnapshotModel:
        with self._unit_of_work_factory() as unit_of_work:
            task = self._require_task(unit_of_work, request.task_id)
            snapshot = unit_of_work.knowledge.get_snapshot(
                request.snapshot_id,
                task_id=request.task_id,
            )
        if snapshot is None:
            raise KeyError(f"Knowledge Snapshot not found: {request.snapshot_id}")
        if (
            task.knowledge_snapshot_id != snapshot.id
            or task.knowledge_snapshot_hash != snapshot.content_hash
        ):
            raise ValueError("Knowledge Snapshot does not match the Task binding")
        return KnowledgeSnapshotModel.model_validate(snapshot)

    def get_manifest(self, request: HarnessManifestGetInput) -> HarnessContextManifestModel:
        with self._unit_of_work_factory() as unit_of_work:
            task = self._require_task(unit_of_work, request.task_id)
            manifest = unit_of_work.knowledge.get_manifest(
                request.manifest_id,
                task_id=request.task_id,
            )
        if manifest is None:
            raise KeyError(f"Harness Manifest not found: {request.manifest_id}")
        if (
            task.harness_manifest_id != manifest.id
            or task.harness_manifest_hash != manifest.content_hash
        ):
            raise ValueError("Harness Manifest does not match the Task binding")
        return HarnessContextManifestModel.model_validate(manifest)

    def search(self, request: KnowledgeSearchInput) -> KnowledgeRevisionPageModel:
        with self._unit_of_work_factory() as unit_of_work:
            snapshot = self._require_bound_snapshot(
                unit_of_work,
                task_id=request.task_id,
                snapshot_id=request.snapshot_id,
            )
            revisions = unit_of_work.knowledge.search_snapshot(
                snapshot_id=request.snapshot_id,
                task_id=request.task_id,
                query=request.query,
                limit=request.limit,
            )
        return KnowledgeRevisionPageModel(
            items=tuple(self._revision_model(revision) for revision in revisions),
            snapshot_id=snapshot.id,
            snapshot_hash=snapshot.content_hash,
        )

    def read(self, request: KnowledgeRevisionReadInput) -> KnowledgeRevisionModel:
        with self._unit_of_work_factory() as unit_of_work:
            self._require_bound_snapshot(
                unit_of_work,
                task_id=request.task_id,
                snapshot_id=request.snapshot_id,
            )
            revision = unit_of_work.knowledge.revision_from_snapshot(
                snapshot_id=request.snapshot_id,
                task_id=request.task_id,
                revision_id=request.revision_id,
            )
        if revision is None:
            raise KeyError(f"Knowledge Revision not found: {request.revision_id}")
        return self._revision_model(revision)

    def links(self, request: KnowledgeRevisionReadInput) -> KnowledgeLinkPageModel:
        revision = self.read(request)
        return KnowledgeLinkPageModel(
            revision_id=revision.id,
            links=revision.links,
            snapshot_id=request.snapshot_id,
        )

    def _context(self, project_id: UUID) -> _ProjectContext:
        with self._unit_of_work_factory() as unit_of_work:
            project = self._require_project(unit_of_work, project_id)
            index = (
                unit_of_work.project_indexes.get(project.active_version_id)
                if project.active_version_id is not None
                else None
            )
            conversations = self._list_conversations(unit_of_work, project.id)
            tasks = self._list_tasks(unit_of_work, project.id)
            artifacts = tuple(
                artifact
                for task in tasks
                for artifact in unit_of_work.state.artifacts_for_task(task.id)
            )
            revisions = unit_of_work.knowledge.current_revisions(project.id)
            sources = unit_of_work.knowledge.list_sources(project.id)
            claims = tuple(
                unit_of_work.memory.claims_for_scope(
                    namespace=MemoryNamespace.PROJECT_CANONICAL,
                    project_id=project.id,
                )
            )
        watermark = self._watermark(
            project=project,
            index=index,
            conversations=conversations,
            tasks=tasks,
            artifacts=artifacts,
            revisions=revisions,
            sources=sources,
            claims=claims,
        )
        source_revision = max(
            project.revision,
            project.metadata_revision,
            index.generation if index is not None else 0,
            *(conversation.revision for conversation in conversations),
            *(task.metadata_revision for task in tasks),
            *(source.revision for source in sources),
            *(source.sync_cursor for source in sources),
            *(claim.current_revision for claim in claims),
        )
        return _ProjectContext(
            project=project,
            index=index,
            conversations=conversations,
            tasks=tasks,
            artifacts=artifacts,
            revisions=revisions,
            sources=sources,
            claims=claims,
            watermark=watermark,
            source_revision=source_revision,
        )

    def _graph(self, context: _ProjectContext) -> KnowledgeGraphModel:
        project = context.project
        index = context.index
        root_id = f"project:{project.id}"
        nodes: list[KnowledgeGraphNodeModel] = [
            KnowledgeGraphNodeModel(
                id=root_id,
                project_id=project.id,
                kind=KnowledgeNodeKind.PROJECT,
                title=project.name,
                revision=project.metadata_revision,
            )
        ]
        edges: list[KnowledgeGraphEdgeModel] = []
        for conversation in context.conversations:
            node_id = f"conversation:{conversation.id}"
            nodes.append(
                KnowledgeGraphNodeModel(
                    id=node_id,
                    project_id=project.id,
                    kind=KnowledgeNodeKind.CONVERSATION,
                    title=conversation.title,
                    conversation_id=conversation.id,
                    revision=conversation.revision,
                )
            )
            edges.append(self._edge(root_id, node_id, KnowledgeRelationKind.CONTAINS))
        for task in context.tasks:
            node_id = f"task:{task.id}"
            nodes.append(
                KnowledgeGraphNodeModel(
                    id=node_id,
                    project_id=project.id,
                    kind=KnowledgeNodeKind.TASK,
                    title=task.display_title or task.user_request[:120],
                    conversation_id=task.conversation_id,
                    task_id=task.id,
                    revision=task.metadata_revision,
                )
            )
            edges.append(
                self._edge(
                    f"conversation:{task.conversation_id}",
                    node_id,
                    KnowledgeRelationKind.CONTAINS,
                )
            )
        for artifact in context.artifacts:
            node_id = f"artifact:{artifact.id}"
            title = str(artifact.metadata.get("title") or artifact.artifact_type.value)
            nodes.append(
                KnowledgeGraphNodeModel(
                    id=node_id,
                    project_id=project.id,
                    kind=KnowledgeNodeKind.ARTIFACT,
                    title=title[:200],
                    content_hash=artifact.content_hash,
                    byte_length=artifact.byte_length,
                    task_id=artifact.task_id,
                    revision=1,
                )
            )
            edges.append(
                self._edge(
                    f"task:{artifact.task_id}",
                    node_id,
                    KnowledgeRelationKind.DERIVED_FROM,
                )
            )
        self._append_workspace_graph(project, index, nodes, edges, root_id)
        self._append_revision_graph(context, nodes, edges, root_id)
        for claim in context.claims:
            node_id = f"memory:{claim.id}"
            nodes.append(
                KnowledgeGraphNodeModel(
                    id=node_id,
                    project_id=project.id,
                    kind=KnowledgeNodeKind.MEMORY,
                    title=f"{claim.subject} {claim.predicate}"[:200],
                    revision=max(claim.current_revision, 1),
                )
            )
            edges.append(self._edge(root_id, node_id, KnowledgeRelationKind.CONTAINS))
        return KnowledgeGraphModel(
            project_id=project.id,
            source_version_id=project.active_version_id,
            source_revision=context.source_revision,
            watermark=context.watermark,
            nodes=tuple(nodes),
            edges=tuple({edge.id: edge for edge in edges}.values()),
        )

    def _append_workspace_graph(
        self,
        project: Project,
        index: ProjectIndex | None,
        nodes: list[KnowledgeGraphNodeModel],
        edges: list[KnowledgeGraphEdgeModel],
        root_id: str,
    ) -> None:
        if index is None:
            return
        file_paths = {item.path for item in index.files}
        folders: set[str] = set()
        for file in index.files:
            folder = str(PurePosixPath(file.path).parent)
            folder = "root" if folder == "." else folder
            if folder not in folders:
                folders.add(folder)
                folder_id = f"folder:{folder}"
                nodes.append(
                    KnowledgeGraphNodeModel(
                        id=folder_id,
                        project_id=project.id,
                        kind=KnowledgeNodeKind.FOLDER,
                        title=folder,
                        relative_path=None if folder == "root" else folder,
                        revision=index.generation,
                    )
                )
                edges.append(self._edge(root_id, folder_id, KnowledgeRelationKind.CONTAINS))
            file_node = self._file_node(project, index, file)
            nodes.append(file_node)
            edges.append(
                self._edge(f"folder:{folder}", file_node.id, KnowledgeRelationKind.CONTAINS)
            )
            for imported in file.imports:
                target = self._resolve_import(file.path, imported, file_paths)
                if target is not None:
                    edges.append(
                        self._edge(
                            file_node.id,
                            f"file:{target}",
                            KnowledgeRelationKind.IMPORTS,
                        )
                    )

    def _append_revision_graph(
        self,
        context: _ProjectContext,
        nodes: list[KnowledgeGraphNodeModel],
        edges: list[KnowledgeGraphEdgeModel],
        root_id: str,
    ) -> None:
        by_title: dict[str, str] = {}
        by_path: dict[str, str] = {}
        for source in context.sources:
            source_node_id = f"knowledge-source:{source.id}"
            nodes.append(
                KnowledgeGraphNodeModel(
                    id=source_node_id,
                    project_id=context.project.id,
                    kind=KnowledgeNodeKind.OBSIDIAN,
                    title=source.display_name,
                    source_id=source.id,
                    revision=source.revision,
                )
            )
            edges.append(self._edge(root_id, source_node_id, KnowledgeRelationKind.CONTAINS))
        for revision in context.revisions:
            node_id = f"knowledge-revision:{revision.id}"
            nodes.append(self._revision_node(revision))
            edges.append(
                self._edge(
                    f"knowledge-source:{revision.source_id}",
                    node_id,
                    KnowledgeRelationKind.CONTAINS,
                )
            )
            by_title[revision.title.casefold()] = node_id
            by_path[revision.relative_path.casefold()] = node_id
            by_path[PurePosixPath(revision.relative_path).stem.casefold()] = node_id
        for revision in context.revisions:
            source_id = f"knowledge-revision:{revision.id}"
            for link in revision.links:
                target_id = by_title.get(link.casefold()) or by_path.get(link.casefold())
                if target_id is not None:
                    edges.append(self._edge(source_id, target_id, KnowledgeRelationKind.REFERENCES))

    @staticmethod
    def _list_conversations(
        unit_of_work: CoreUnitOfWork,
        project_id: UUID,
    ) -> tuple[Conversation, ...]:
        items: list[Conversation] = []
        cursor: str | None = None
        while True:
            page = unit_of_work.state.list_conversations(
                project_id=project_id,
                limit=100,
                cursor=cursor,
            )
            items.extend(page.items)
            cursor = page.next_cursor
            if cursor is None:
                return tuple(items)

    @staticmethod
    def _list_tasks(unit_of_work: CoreUnitOfWork, project_id: UUID) -> tuple[Task, ...]:
        items: list[Task] = []
        cursor: str | None = None
        while True:
            page = unit_of_work.state.list_tasks(
                project_id=project_id,
                conversation_id=None,
                limit=100,
                cursor=cursor,
            )
            items.extend(page.items)
            cursor = page.next_cursor
            if cursor is None:
                return tuple(items)

    def _file_items(
        self,
        project: Project,
        index: ProjectIndex | None,
    ) -> tuple[KnowledgeItemModel, ...]:
        if index is None:
            return ()
        return tuple(
            KnowledgeItemModel.model_validate(
                self._file_node(project, index, item).model_dump(
                    exclude={"conversation_id", "task_id", "source_id", "revision_id"}
                )
            )
            for item in index.files
        )

    def _revision_items(
        self,
        revisions: tuple[KnowledgeRevision, ...],
    ) -> tuple[KnowledgeItemModel, ...]:
        return tuple(
            KnowledgeItemModel.model_validate(
                self._revision_node(revision).model_dump(
                    exclude={"conversation_id", "task_id", "source_id", "revision_id"}
                )
            )
            for revision in revisions
        )

    @staticmethod
    def _file_node(
        project: Project,
        index: ProjectIndex,
        item: ProjectFile,
    ) -> KnowledgeGraphNodeModel:
        note = item.path.casefold().endswith((".md", ".mdx"))
        return KnowledgeGraphNodeModel(
            id=f"file:{item.path}",
            project_id=project.id,
            kind=KnowledgeNodeKind.NOTE if note else KnowledgeNodeKind.FILE,
            title=PurePosixPath(item.path).name,
            relative_path=item.path,
            content_hash=item.content_hash,
            byte_length=item.byte_length,
            language=item.language,
            revision=index.generation,
        )

    @staticmethod
    def _revision_node(revision: KnowledgeRevision) -> KnowledgeGraphNodeModel:
        return KnowledgeGraphNodeModel(
            id=f"knowledge-revision:{revision.id}",
            project_id=revision.project_id,
            kind=KnowledgeNodeKind.NOTE,
            title=revision.title,
            relative_path=revision.relative_path,
            content_hash=revision.content_hash,
            byte_length=len(revision.content.encode("utf-8")),
            revision=revision.revision,
            source_id=revision.source_id,
            revision_id=revision.id,
        )

    @staticmethod
    def _revision_model(revision: KnowledgeRevision) -> KnowledgeRevisionModel:
        return KnowledgeRevisionModel.model_validate(revision)

    @staticmethod
    def _edge(
        source_id: str,
        target_id: str,
        relation: KnowledgeRelationKind,
    ) -> KnowledgeGraphEdgeModel:
        digest = hashlib.sha256(f"{source_id}\0{relation.value}\0{target_id}".encode()).hexdigest()
        return KnowledgeGraphEdgeModel(
            id=digest,
            source_id=source_id,
            target_id=target_id,
            relation=relation,
        )

    @staticmethod
    def _resolve_import(source: str, imported: str, paths: set[str]) -> str | None:
        normalized = imported.replace("\\", "/")
        candidates = [normalized]
        if normalized.startswith("."):
            candidates.append(str(PurePosixPath(source).parent.joinpath(normalized)))
        extensions = ("", ".ts", ".tsx", ".js", ".jsx", ".py", "/index.ts", "/index.js")
        for candidate in candidates:
            collapsed = str(PurePosixPath(candidate))
            for extension in extensions:
                resolved = f"{collapsed}{extension}"
                if resolved in paths:
                    return resolved
        return None

    @staticmethod
    def _source_health(statuses: set[KnowledgeSourceStatus]) -> str:
        if not statuses:
            return "not_connected"
        if KnowledgeSourceStatus.FAILED in statuses:
            return "failed"
        if KnowledgeSourceStatus.PARTIAL in statuses:
            return "partial"
        if KnowledgeSourceStatus.SYNCING in statuses:
            return "syncing"
        if statuses <= {KnowledgeSourceStatus.READY}:
            return "ready"
        return "configured"

    @staticmethod
    def _require_project(unit_of_work: CoreUnitOfWork, project_id: UUID) -> Project:
        project = unit_of_work.state.get_project(project_id)
        if project is None or project.deleted_at is not None or project.purged_at is not None:
            raise KeyError(f"project not found: {project_id}")
        return project

    @staticmethod
    def _require_task(unit_of_work: CoreUnitOfWork, task_id: UUID) -> Task:
        task = unit_of_work.state.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        return task

    def _require_bound_snapshot(
        self,
        unit_of_work: CoreUnitOfWork,
        *,
        task_id: UUID,
        snapshot_id: UUID,
    ) -> KnowledgeSnapshot:
        task = self._require_task(unit_of_work, task_id)
        snapshot = unit_of_work.knowledge.get_snapshot(snapshot_id, task_id=task_id)
        if snapshot is None:
            raise KeyError(f"Knowledge Snapshot not found: {snapshot_id}")
        if (
            task.knowledge_snapshot_id != snapshot.id
            or task.knowledge_snapshot_hash != snapshot.content_hash
        ):
            raise ValueError("Knowledge Snapshot does not match the Task binding")
        return snapshot

    @staticmethod
    def _watermark(
        *,
        project: Project,
        index: ProjectIndex | None,
        conversations: tuple[Conversation, ...],
        tasks: tuple[Task, ...],
        artifacts: tuple[Artifact, ...],
        revisions: tuple[KnowledgeRevision, ...],
        sources: tuple[KnowledgeSource, ...],
        claims: tuple[MemoryClaim, ...],
    ) -> str:
        payload = {
            "version": 1,
            "project": [
                str(project.id),
                project.revision,
                project.metadata_revision,
                str(project.active_version_id) if project.active_version_id else None,
            ],
            "workspace": ([index.generation, index.source_hash] if index is not None else None),
            "conversations": [
                [str(item.id), item.revision, item.updated_at.isoformat()]
                for item in sorted(conversations, key=lambda value: str(value.id))
            ],
            "tasks": [
                [
                    str(item.id),
                    item.metadata_revision,
                    item.status.value,
                    item.updated_at.isoformat(),
                ]
                for item in sorted(tasks, key=lambda value: str(value.id))
            ],
            "artifacts": [
                [str(item.id), item.content_hash]
                for item in sorted(artifacts, key=lambda value: str(value.id))
            ],
            "sources": [
                [
                    str(item.id),
                    item.revision,
                    item.sync_cursor,
                    item.status.value,
                ]
                for item in sorted(sources, key=lambda value: str(value.id))
            ],
            "knowledge": [
                [str(item.id), item.revision_hash]
                for item in sorted(revisions, key=lambda value: str(value.id))
            ],
            "memory": [
                [
                    str(item.id),
                    item.current_revision,
                    item.status.value,
                    item.updated_at.isoformat(),
                ]
                for item in sorted(claims, key=lambda value: str(value.id))
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()


__all__ = ["ProjectKnowledgeApplication"]
