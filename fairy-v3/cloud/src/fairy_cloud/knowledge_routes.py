from collections.abc import Callable
from typing import Any
from uuid import UUID

from fairy_core.contracts.knowledge import (
    HarnessContextManifestModel,
    HarnessManifestGetInput,
    KnowledgeCollectionPageModel,
    KnowledgeGraphModel,
    KnowledgeItemListInput,
    KnowledgeItemPageModel,
    KnowledgeLinkPageModel,
    KnowledgeProjectInput,
    KnowledgeRevisionModel,
    KnowledgeRevisionPageModel,
    KnowledgeRevisionReadInput,
    KnowledgeSearchInput,
    KnowledgeSnapshotGetInput,
    KnowledgeSnapshotModel,
    KnowledgeSourcePageModel,
    ProjectKnowledgeOverviewModel,
)
from fastapi import APIRouter


def install_knowledge_routes(
    router: APIRouter,
    invoke: Callable[[str, dict[str, Any]], Any],
) -> None:
    @router.get(
        "/projects/{project_id}/knowledge",
        operation_id="knowledge.projects.overview",
        response_model=ProjectKnowledgeOverviewModel,
    )
    def get_project_knowledge(project_id: UUID) -> dict[str, Any]:
        request = KnowledgeProjectInput(project_id=project_id)
        return invoke("knowledge.projects.overview", request.model_dump(mode="json"))

    @router.get(
        "/projects/{project_id}/knowledge/items",
        operation_id="knowledge.items.list",
        response_model=KnowledgeItemPageModel,
    )
    def list_project_knowledge_items(
        project_id: UUID,
        query: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        request = KnowledgeItemListInput(project_id=project_id, query=query, limit=limit)
        return invoke("knowledge.items.list", request.model_dump(mode="json"))

    @router.get(
        "/projects/{project_id}/knowledge/graph",
        operation_id="knowledge.graph.get",
        response_model=KnowledgeGraphModel,
    )
    def get_project_knowledge_graph(project_id: UUID) -> dict[str, Any]:
        request = KnowledgeProjectInput(project_id=project_id)
        return invoke("knowledge.graph.get", request.model_dump(mode="json"))

    @router.get(
        "/projects/{project_id}/knowledge/sources",
        operation_id="knowledge.sources.list",
        response_model=KnowledgeSourcePageModel,
    )
    def list_project_knowledge_sources(project_id: UUID) -> dict[str, Any]:
        request = KnowledgeProjectInput(project_id=project_id)
        return invoke("knowledge.sources.list", request.model_dump(mode="json"))

    @router.get(
        "/projects/{project_id}/knowledge/collections",
        operation_id="knowledge.collections.list",
        response_model=KnowledgeCollectionPageModel,
    )
    def list_project_knowledge_collections(project_id: UUID) -> dict[str, Any]:
        request = KnowledgeProjectInput(project_id=project_id)
        return invoke("knowledge.collections.list", request.model_dump(mode="json"))

    @router.get(
        "/knowledge/snapshots/{snapshot_id}",
        operation_id="knowledge.snapshots.get",
        response_model=KnowledgeSnapshotModel,
    )
    def get_knowledge_snapshot(snapshot_id: UUID, task_id: UUID) -> dict[str, Any]:
        request = KnowledgeSnapshotGetInput(task_id=task_id, snapshot_id=snapshot_id)
        return invoke("knowledge.snapshots.get", request.model_dump(mode="json"))

    @router.get(
        "/harness/manifests/{manifest_id}",
        operation_id="harness.manifests.get",
        response_model=HarnessContextManifestModel,
    )
    def get_harness_manifest(manifest_id: UUID, task_id: UUID) -> dict[str, Any]:
        request = HarnessManifestGetInput(task_id=task_id, manifest_id=manifest_id)
        return invoke("harness.manifests.get", request.model_dump(mode="json"))

    @router.post(
        "/knowledge/search",
        operation_id="knowledge.search",
        response_model=KnowledgeRevisionPageModel,
    )
    def search_knowledge(request: KnowledgeSearchInput) -> dict[str, Any]:
        return invoke("knowledge.search", request.model_dump(mode="json"))

    @router.get(
        "/knowledge/revisions/{revision_id}",
        operation_id="knowledge.read",
        response_model=KnowledgeRevisionModel,
    )
    def read_knowledge_revision(
        revision_id: UUID,
        task_id: UUID,
        snapshot_id: UUID,
    ) -> dict[str, Any]:
        request = KnowledgeRevisionReadInput(
            task_id=task_id,
            snapshot_id=snapshot_id,
            revision_id=revision_id,
        )
        return invoke("knowledge.read", request.model_dump(mode="json"))

    @router.get(
        "/knowledge/revisions/{revision_id}/links",
        operation_id="knowledge.links",
        response_model=KnowledgeLinkPageModel,
    )
    def list_knowledge_revision_links(
        revision_id: UUID,
        task_id: UUID,
        snapshot_id: UUID,
    ) -> dict[str, Any]:
        request = KnowledgeRevisionReadInput(
            task_id=task_id,
            snapshot_id=snapshot_id,
            revision_id=revision_id,
        )
        return invoke("knowledge.links", request.model_dump(mode="json"))


__all__ = ["install_knowledge_routes"]
