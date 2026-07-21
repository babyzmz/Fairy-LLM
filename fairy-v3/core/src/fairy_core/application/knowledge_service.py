from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast

from pydantic import BaseModel

from fairy_core.contracts.knowledge import (
    HarnessManifestGetInput,
    KnowledgeItemListInput,
    KnowledgeProjectInput,
    KnowledgeRevisionReadInput,
    KnowledgeSearchInput,
    KnowledgeSnapshotGetInput,
)
from fairy_core.knowledge.application import ProjectKnowledgeApplication


def knowledge_service_handlers(
    application: ProjectKnowledgeApplication,
) -> Mapping[str, Callable[[BaseModel], Any]]:
    return {
        "knowledge.projects.overview": lambda request: application.overview(
            cast(KnowledgeProjectInput, request)
        ),
        "knowledge.items.list": lambda request: application.list_items(
            cast(KnowledgeItemListInput, request)
        ),
        "knowledge.graph.get": lambda request: application.graph(
            cast(KnowledgeProjectInput, request)
        ),
        "knowledge.sources.list": lambda request: application.list_sources(
            cast(KnowledgeProjectInput, request)
        ),
        "knowledge.collections.list": lambda request: application.list_collections(
            cast(KnowledgeProjectInput, request)
        ),
        "knowledge.snapshots.get": lambda request: application.get_snapshot(
            cast(KnowledgeSnapshotGetInput, request)
        ),
        "harness.manifests.get": lambda request: application.get_manifest(
            cast(HarnessManifestGetInput, request)
        ),
        "knowledge.search": lambda request: application.search(cast(KnowledgeSearchInput, request)),
        "knowledge.read": lambda request: application.read(
            cast(KnowledgeRevisionReadInput, request)
        ),
        "knowledge.links": lambda request: application.links(
            cast(KnowledgeRevisionReadInput, request)
        ),
    }


__all__ = ["knowledge_service_handlers"]
