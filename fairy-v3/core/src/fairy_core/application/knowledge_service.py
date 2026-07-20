from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast

from pydantic import BaseModel

from fairy_core.contracts.knowledge import KnowledgeItemListInput, KnowledgeProjectInput
from fairy_core.knowledge import ProjectKnowledgeApplication


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
    }


__all__ = ["knowledge_service_handlers"]
