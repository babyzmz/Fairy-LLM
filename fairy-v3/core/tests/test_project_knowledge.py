from pathlib import Path

from fairy_core.application.core import CoreApplication
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.knowledge import KnowledgeItemListInput, KnowledgeProjectInput
from fairy_core.domain.models import ProjectResidency
from fairy_core.knowledge import ProjectKnowledgeApplication
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from fairy_core.workspace.index import ProjectIndexer


def test_project_knowledge_uses_active_immutable_project_index(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    registry = build_default_registry()
    core = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(tmp_path / "managed"),
        registry=registry,
        policy=PolicyEngine(registry),
    )
    context = core.create_project(name="Knowledge project", residency=ProjectResidency.LOCAL_ONLY)
    root = Path(context.initial_version.project_root)
    (root / "src").mkdir()
    (root / "src" / "main.ts").write_text("import './theme.css';", encoding="utf-8")
    (root / "src" / "theme.css").write_text("body { color: white; }", encoding="utf-8")
    (root / "README.md").write_text("# Knowledge project", encoding="utf-8")
    index = ProjectIndexer().build(
        project_id=context.project.id,
        workspace_id=context.project.workspace_id,
        version_id=context.initial_version.id,
        root=root,
        generation=2,
    )
    with factory() as unit_of_work:
        unit_of_work.project_indexes.replace_generation(index, expected_generation=1)
        unit_of_work.commit()

    knowledge = ProjectKnowledgeApplication(factory)
    overview = knowledge.overview(KnowledgeProjectInput(project_id=context.project.id))
    items = knowledge.list_items(
        KnowledgeItemListInput(project_id=context.project.id, query="readme")
    )
    graph = knowledge.graph(KnowledgeProjectInput(project_id=context.project.id))

    assert overview.file_count == 3
    assert overview.note_count == 1
    assert not overview.obsidian_connected
    assert [item.relative_path for item in items.items] == ["README.md"]
    assert {node.kind.value for node in graph.nodes} >= {"project", "folder", "file", "note"}
    assert any(edge.relation.value == "imports" for edge in graph.edges)
    engine.dispose()


def test_project_knowledge_graph_does_not_include_another_project_index(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    registry = build_default_registry()
    core = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(tmp_path / "managed"),
        registry=registry,
        policy=PolicyEngine(registry),
    )
    first = core.create_project(name="First project", residency=ProjectResidency.LOCAL_ONLY)
    second = core.create_project(name="Second project", residency=ProjectResidency.LOCAL_ONLY)
    first_root = Path(first.initial_version.project_root)
    second_root = Path(second.initial_version.project_root)
    (first_root / "first.md").write_text("# First", encoding="utf-8")
    (second_root / "private.db").write_bytes(b"second-project-only")
    first_index = ProjectIndexer().build(
        project_id=first.project.id,
        workspace_id=first.project.workspace_id,
        version_id=first.initial_version.id,
        root=first_root,
        generation=2,
    )
    second_index = ProjectIndexer().build(
        project_id=second.project.id,
        workspace_id=second.project.workspace_id,
        version_id=second.initial_version.id,
        root=second_root,
        generation=2,
    )
    with factory() as unit_of_work:
        unit_of_work.project_indexes.replace_generation(first_index, expected_generation=1)
        unit_of_work.project_indexes.replace_generation(second_index, expected_generation=1)
        unit_of_work.commit()

    graph = ProjectKnowledgeApplication(factory).graph(
        KnowledgeProjectInput(project_id=first.project.id)
    )
    paths = {node.relative_path for node in graph.nodes if node.relative_path is not None}

    assert "first.md" in paths
    assert "private.db" not in paths
    assert all(node.project_id == first.project.id for node in graph.nodes)
    engine.dispose()
