import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from fairy_core.contracts.methods import CORE_METHODS, CoreMethodTransport
from fairy_core.contracts.obsidian import (
    ObsidianReadScope,
    ObsidianSourceCreateInput,
    ObsidianSourceListInput,
    ObsidianSourceMode,
    ObsidianSourceSyncInput,
    ObsidianVaultItemReadInput,
)
from fairy_core.domain.errors import VersionConflictError
from fairy_core.domain.ids import new_id
from fairy_core.obsidian import ObsidianConnector, ObsidianPathRegistry
from fairy_core.transports.stdio import build_local_service


def _connector_for_vault(
    tmp_path: Path,
    vault: Path,
) -> tuple[ObsidianConnector, str]:
    path_registry_path = tmp_path / "obsidian-paths.json"
    token = ObsidianPathRegistry(path_registry_path).register(vault)
    return (
        ObsidianConnector(
            registry_path=tmp_path / "sources.json",
            path_registry_path=path_registry_path,
        ),
        token,
    )


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
    connector, local_path_token = _connector_for_vault(tmp_path, vault)
    project_id = new_id()
    request = ObsidianSourceCreateInput(
        project_id=project_id,
        display_name="Test Vault",
        local_path_token=local_path_token,
        read_scope=ObsidianReadScope.SELECTED_DIRECTORIES,
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
    assert str(vault) not in (tmp_path / "sources.json").read_text(encoding="utf-8")

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
    connector, local_path_token = _connector_for_vault(tmp_path, vault)

    with pytest.raises(ValueError, match="Vault-relative"):
        connector.create_source(
            ObsidianSourceCreateInput(
                project_id=new_id(),
                display_name="Unsafe Vault",
                local_path_token=local_path_token,
                read_scope=ObsidianReadScope.SELECTED_DIRECTORIES,
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
    data_dir = tmp_path / "data"
    local_path_token = ObsidianPathRegistry(data_dir / "obsidian-paths.json").register(vault)
    service = build_local_service(data_dir)
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
                "local_path_token": local_path_token,
                "read_scope": "whole_vault",
                "allowed_directories": [],
                "whole_vault_confirmed": True,
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


def test_selected_directory_scope_requires_an_explicit_directory() -> None:
    with pytest.raises(ValidationError, match="Select at least one Vault directory"):
        ObsidianSourceCreateInput(
            project_id=new_id(),
            display_name="Unsafe implicit whole Vault",
            local_path_token=str(new_id()),
            allowed_directories=(),
            idempotency_key="source:implicit-whole-vault",
        )


def test_whole_vault_scope_requires_separate_confirmation() -> None:
    with pytest.raises(ValidationError, match="explicit confirmation"):
        ObsidianSourceCreateInput(
            project_id=new_id(),
            display_name="Unconfirmed whole Vault",
            local_path_token=str(new_id()),
            read_scope=ObsidianReadScope.WHOLE_VAULT,
            allowed_directories=(),
            idempotency_key="source:unconfirmed-whole-vault",
        )


def test_source_rejects_an_unavailable_local_path_token(tmp_path: Path) -> None:
    connector = ObsidianConnector(
        registry_path=tmp_path / "sources.json",
        path_registry_path=tmp_path / "obsidian-paths.json",
    )
    with pytest.raises(KeyError, match="local path token is unavailable"):
        connector.create_source(
            ObsidianSourceCreateInput(
                project_id=new_id(),
                display_name="Missing local Vault",
                local_path_token=str(new_id()),
                allowed_directories=("Notes",),
                idempotency_key="source:missing-local-vault",
            )
        )


def test_obsidian_rpc_methods_are_device_local_only() -> None:
    obsidian_methods = {
        name: method
        for name, method in CORE_METHODS.items()
        if name.startswith("obsidian.")
    }

    assert obsidian_methods
    assert all(
        method.transport is CoreMethodTransport.LOCAL_ONLY
        for method in obsidian_methods.values()
    )


def test_sync_rejects_a_reparse_directory_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = tmp_path / "vault"
    notes = vault / "Notes"
    notes.mkdir(parents=True)
    (notes / "Unsafe.md").write_text("# Unsafe", encoding="utf-8")
    connector, local_path_token = _connector_for_vault(tmp_path, vault)
    source = connector.create_source(
        ObsidianSourceCreateInput(
            project_id=new_id(),
            display_name="Reparse Vault",
            local_path_token=local_path_token,
            allowed_directories=("Notes",),
            idempotency_key="source:reparse-vault",
        )
    )
    original = connector._is_reparse
    monkeypatch.setattr(
        connector,
        "_is_reparse",
        lambda path: path == notes or original(path),
    )

    result = connector.sync(
        ObsidianSourceSyncInput(source_id=source.id, expected_revision=source.revision)
    )

    assert result.scanned_count == 0
    assert result.failed_count == 1
    assert result.source.status == "partial"


def test_legacy_source_paths_are_migrated_out_of_the_domain_registry(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    notes = vault / "Notes"
    notes.mkdir(parents=True)
    source_id = str(new_id())
    project_id = new_id()
    now = "2026-07-21T00:00:00+00:00"
    source_registry = tmp_path / "sources.json"
    source_registry.write_text(
        json.dumps(
            {
                "sources": {
                    source_id: {
                        "id": source_id,
                        "project_id": str(project_id),
                        "display_name": "Legacy Vault",
                        "vault_path": str(vault),
                        "allowed_directories": ["Notes"],
                        "managed_directory": "Fairy",
                        "mode": "read_only",
                        "status": "configured",
                        "revision": 1,
                        "items": {},
                        "last_synced_at": None,
                        "created_at": now,
                        "updated_at": now,
                    }
                },
                "requests": {},
            }
        ),
        encoding="utf-8",
    )
    connector = ObsidianConnector(
        registry_path=source_registry,
        path_registry_path=tmp_path / "obsidian-paths.json",
    )

    page = connector.list_sources(ObsidianSourceListInput(project_id=project_id))

    assert page.items[0].read_scope is ObsidianReadScope.SELECTED_DIRECTORIES
    persisted = source_registry.read_text(encoding="utf-8")
    assert "vault_path" not in persisted
    assert str(vault) not in persisted
