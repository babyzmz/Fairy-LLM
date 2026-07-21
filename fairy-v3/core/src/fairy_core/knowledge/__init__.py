from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fairy_core.knowledge.application import ProjectKnowledgeApplication
    from fairy_core.knowledge.harness import HarnessManifestBuilder, KnowledgeSnapshotBuilder
    from fairy_core.knowledge.sync import ObsidianKnowledgeSync

__all__ = [
    "HarnessManifestBuilder",
    "KnowledgeSnapshotBuilder",
    "ObsidianKnowledgeSync",
    "ProjectKnowledgeApplication",
]


def __getattr__(name: str) -> Any:
    if name == "ProjectKnowledgeApplication":
        from fairy_core.knowledge.application import ProjectKnowledgeApplication

        return ProjectKnowledgeApplication
    if name in {"HarnessManifestBuilder", "KnowledgeSnapshotBuilder"}:
        from fairy_core.knowledge.harness import HarnessManifestBuilder, KnowledgeSnapshotBuilder

        return {
            "HarnessManifestBuilder": HarnessManifestBuilder,
            "KnowledgeSnapshotBuilder": KnowledgeSnapshotBuilder,
        }[name]
    if name == "ObsidianKnowledgeSync":
        from fairy_core.knowledge.sync import ObsidianKnowledgeSync

        return ObsidianKnowledgeSync
    raise AttributeError(name)
