from pathlib import Path

import pytest

from fairy_core.contracts.obsidian import (
    ObsidianSourceCreateInput,
    ObsidianSourceListInput,
    ObsidianSourceMode,
    ObsidianSourceSyncInput,
    ObsidianVaultItemReadInput,
)
from fairy_core.domain.errors import VersionConflictError
from fairy_core.domain.ids import new_id
from fairy_core.obsidian import ObsidianConnector
from fairy_core.transports.stdio import build_local_service


def test_obsidian_health_distinguishes_desktop_and_cli(tmp_path: Path) -> None:
    local_app_data = tmp_path / "local"
    desktop = local_app_data / "Programs" / "Obsidian" / "Obsidian.exe"
    desktop.parent.mkdir(parents=True)
    desktop.write_bytes(b"placeholder")
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()

    health = ObsidianConnector(
        {
            "LOCALAPPDATA": str(local_app_data),
            "PROGRAMFILES": str(tmp_path),
            "PATH": str(empty_path),
        }
    ).health()

    assert health.desktop_installed
    assert not health.cli_available
    assert health.status == "cli_disabled"


def test_obsidian_health_reports_missing_installation(tmp_path: Path) -> None:
    health = ObsidianConnector(
        {"LOCALAPPDATA": str(tmp_path), "PROGRAMFILES": str(tmp_path), "PATH": str(tmp_path)}
    ).health()

    assert not health.desktop_installed
    assert not health.cli_available
    assert health.status == "not_installed"


def test_obsidian_source_scans_authorized_notes_and_wikilinks(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    notes = vault / "Notes"
    excluded = vault / ".obsidian"
    notes.mkdir(parents=True)
    excluded.mkdir()
    (notes / "Architecture.md").write_text(
        "# Architecture\nRelated to [[Project Plan]] and [[API]].",
        encoding="utf-8",
    )
    (notes / "Project Plan.md").write_text("# Project Plan", encoding="utf-8")
    (excluded / "workspace.json").write_text("{}", encoding="utf-8")
    connector = ObsidianConnector(registry_path=tmp_path / "sources.json")
    project_id = new_id()
    request = ObsidianSourceCreateInput(
        project_id=project_id,
        display_name="Test Vault",
        vault_path=str(vault),
        allowed_directories=("Notes",),
        managed_directory="Fairy",
        mode=ObsidianSourceMode.BIDIRECTIONAL,
        idempotency_key="source:test-vault",
    )

    source = connector.create_source(request)
    assert connector.create_source(request) == source
    result = connector.sync(
        ObsidianSourceSyncInput(source_id=source.id, expected_revision=source.revision)
    )
    items = connector.list_items(source.id)

    assert result.scanned_count == 2
    assert result.changed_count == 2
    assert result.deleted_count == 0
    assert result.source.revision == 2
    assert connector.list_sources(ObsidianSourceListInput(project_id=project_id)).items == (
        result.source,
    )
    architecture = next(item for item in items.items if item.title == "Architecture")
    assert architecture.links == ("API", "Project Plan")
    content = connector.read_item(
        ObsidianVaultItemReadInput(
            source_id=source.id,
            relative_path=architecture.relative_path,
            expected_source_revision=result.source.revision,
            expected_content_hash=architecture.content_hash,
        )
    )
    assert content.content.startswith("# Architecture")

    (notes / "Architecture.md").write_text("# Replaced", encoding="utf-8")
    with pytest.raises(VersionConflictError, match="changed since it was indexed"):
        connector.read_item(
            ObsidianVaultItemReadInput(
                source_id=source.id,
                relative_path=architecture.relative_path,
                expected_source_revision=result.source.revision,
                expected_content_hash=architecture.content_hash,
            )
        )


def test_obsidian_source_rejects_parent_directory_escape(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    connector = ObsidianConnector(registry_path=tmp_path / "sources.json")

    with pytest.raises(ValueError, match="Vault-relative"):
        connector.create_source(
            ObsidianSourceCreateInput(
                project_id=new_id(),
                display_name="Unsafe Vault",
                vault_path=str(vault),
                allowed_directories=("../outside",),
                idempotency_key="source:unsafe",
            )
        )


def test_local_service_composes_obsidian_project_source(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Architecture.md").write_text(
        "# Architecture\nSee [[Project Plan]].",
        encoding="utf-8",
    )
    (vault / "Project Plan.md").write_text("# Project Plan", encoding="utf-8")
    service = build_local_service(tmp_path / "data")
    try:
        project = service.invoke(
            "projects.create",
            {"name": "Knowledge project", "residency": "local_only"},
        )["project"]
        source = service.invoke(
            "obsidian.sources.create",
            {
                "project_id": project["id"],
                "display_name": "Project Vault",
                "vault_path": str(vault),
                "allowed_directories": [],
                "managed_directory": "Fairy",
                "mode": "read_only",
                "idempotency_key": "source:service-project-vault",
            },
        )

        result = service.invoke(
            "obsidian.sync.start",
            {"source_id": source["id"], "expected_revision": source["revision"]},
        )
        items = service.invoke(
        "obsidian.sources.items.list",
            {"source_id": source["id"]},
        )["items"]

        content = service.invoke(
            "obsidian.sources.items.read",
            {
                "source_id": source["id"],
                "relative_path": items[0]["relative_path"],
                "expected_source_revision": result["source"]["revision"],
                "expected_content_hash": items[0]["content_hash"],
            },
        )

        assert result["scanned_count"] == 2
        assert {item["relative_path"] for item in items} == {
            "Architecture.md",
            "Project Plan.md",
        }
        architecture = next(item for item in items if item["title"] == "Architecture")
        assert architecture["links"] == ["Project Plan"]
        assert content["content"].startswith("# ")
    finally:
        service.close()
