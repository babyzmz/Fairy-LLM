from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from uuid import UUID

from fairy_core.contracts.knowledge import (
    KnowledgeGraphEdgeModel,
    KnowledgeGraphModel,
    KnowledgeGraphNodeModel,
    KnowledgeItemListInput,
    KnowledgeItemModel,
    KnowledgeItemPageModel,
    KnowledgeNodeKind,
    KnowledgeProjectInput,
    KnowledgeRelationKind,
    ProjectKnowledgeOverviewModel,
)
from fairy_core.domain.models import Conversation, Project
from fairy_core.persistence.unit_of_work import CoreUnitOfWork, CoreUnitOfWorkFactory
from fairy_core.workspace.models import ProjectFile, ProjectIndex


class ProjectKnowledgeApplication:
    def __init__(self, unit_of_work_factory: CoreUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def overview(self, request: KnowledgeProjectInput) -> ProjectKnowledgeOverviewModel:
        project, index, conversation_count = self._context(request.project_id)
        items = self._file_items(project, index)
        graph = self._graph(project, index)
        return ProjectKnowledgeOverviewModel(
            project_id=project.id,
            source_version_id=project.active_version_id,
            source_revision=index.generation if index is not None else project.revision,
            file_count=len(items),
            note_count=sum(item.kind is KnowledgeNodeKind.NOTE for item in items),
            conversation_count=conversation_count,
            relation_count=len(graph.edges),
            obsidian_connected=False,
            obsidian_health="not_connected",
        )

    def list_items(self, request: KnowledgeItemListInput) -> KnowledgeItemPageModel:
        project, index, _conversation_count = self._context(request.project_id)
        items = self._file_items(project, index)
        query = " ".join((request.query or "").casefold().split())
        if query:
            items = tuple(
                item
                for item in items
                if query
                in (
                    f"{item.title} {item.relative_path or ''} {item.language or ''}"
                ).casefold()
            )
        revision = index.generation if index is not None else project.revision
        return KnowledgeItemPageModel(items=items[: request.limit], source_revision=revision)

    def graph(self, request: KnowledgeProjectInput) -> KnowledgeGraphModel:
        project, index, _conversation_count = self._context(request.project_id)
        return self._graph(project, index)

    def _context(self, project_id: UUID) -> tuple[Project, ProjectIndex | None, int]:
        with self._unit_of_work_factory() as unit_of_work:
            project = unit_of_work.state.get_project(project_id)
            if project is None or project.deleted_at is not None or project.purged_at is not None:
                raise KeyError(f"project not found: {project_id}")
            index = (
                unit_of_work.project_indexes.get(project.active_version_id)
                if project.active_version_id is not None
                else None
            )
            conversations = self._list_conversations(unit_of_work, project.id)
            conversation_count = len(conversations)
        return project, index, conversation_count

    def _graph(self, project: Project, index: ProjectIndex | None) -> KnowledgeGraphModel:
        revision = index.generation if index is not None else project.revision
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
        with self._unit_of_work_factory() as unit_of_work:
            conversations = self._list_conversations(unit_of_work, project.id)
        for conversation in conversations:
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
        if index is not None:
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
        return KnowledgeGraphModel(
            project_id=project.id,
            source_version_id=project.active_version_id,
            source_revision=revision,
            nodes=tuple(nodes),
            edges=tuple({edge.id: edge for edge in edges}.values()),
        )

    @staticmethod
    def _list_conversations(
        unit_of_work: CoreUnitOfWork,
        project_id: UUID,
    ) -> tuple[Conversation, ...]:
        state = unit_of_work.state
        items: list[Conversation] = []
        cursor: str | None = None
        while True:
            page = state.list_conversations(
                project_id=project_id,
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
                self._file_node(project, index, item).model_dump(exclude={"conversation_id"})
            )
            for item in index.files
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


__all__ = ["ProjectKnowledgeApplication"]
