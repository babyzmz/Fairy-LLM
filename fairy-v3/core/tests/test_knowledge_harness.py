from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from fairy_core.obsidian.path_registry import ObsidianPathRegistry
from fairy_core.providers import ProviderCapability
from fairy_core.transports.stdio import build_local_service


def test_obsidian_revisions_bind_an_immutable_task_harness(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "Architecture.md"
    note.write_text(
        "---\nowner: user\n---\n# Architecture\nSee [[Project Plan]].",
        encoding="utf-8",
    )
    (vault / "Project Plan.md").write_text("# Project Plan\nMilestone one.", encoding="utf-8")
    data_dir = tmp_path / "data"
    path_token = ObsidianPathRegistry(data_dir / "obsidian-paths.json").register(vault)
    service = build_local_service(data_dir)
    try:
        context = service.invoke(
            "projects.create",
            {"name": "Knowledge project", "residency": "local_only"},
        )
        project = context["project"]
        conversation = service.invoke(
            "conversations.create",
            {"project_id": project["id"], "workspace_type": "project_chat"},
        )
        source = service.invoke(
            "obsidian.sources.create",
            {
                "project_id": project["id"],
                "display_name": "Project Vault",
                "local_path_token": path_token,
                "read_scope": "whole_vault",
                "allowed_directories": [],
                "whole_vault_confirmed": True,
                "managed_directory": "Fairy",
                "mode": "read_only",
                "idempotency_key": "source:knowledge-harness",
            },
        )
        first_sync = service.invoke(
            "obsidian.sync.start",
            {"source_id": source["id"], "expected_revision": source["revision"]},
        )
        first = _task_and_turn(service, conversation["id"], "first")

        snapshot = service.invoke(
            "knowledge.snapshots.get",
            {
                "task_id": first["task"]["id"],
                "snapshot_id": first["turn"]["knowledge_snapshot_id"],
            },
        )
        manifest = service.invoke(
            "harness.manifests.get",
            {
                "task_id": first["task"]["id"],
                "manifest_id": first["turn"]["harness_manifest_id"],
            },
        )
        search = service.invoke(
            "knowledge.search",
            {
                "task_id": first["task"]["id"],
                "snapshot_id": snapshot["id"],
                "query": "Milestone one",
                "limit": 10,
            },
        )

        assert len(snapshot["items"]) == 2
        assert len(search["items"]) == 1
        assert manifest["knowledge_snapshot_hash"] == snapshot["content_hash"]
        assert manifest["memory_snapshot_hash"] == first["turn"]["memory_snapshot_hash"]
        assert str(vault) not in json.dumps(
            {"snapshot": snapshot, "manifest": manifest, "search": search}
        )

        note.write_text("# Architecture\nNew fact only in the live Vault.", encoding="utf-8")
        service.invoke(
            "obsidian.sync.start",
            {
                "source_id": source["id"],
                "expected_revision": first_sync["source"]["revision"],
            },
        )
        old_search = service.invoke(
            "knowledge.search",
            {
                "task_id": first["task"]["id"],
                "snapshot_id": snapshot["id"],
                "query": "New fact only",
                "limit": 10,
            },
        )
        second = _task_and_turn(service, conversation["id"], "second")
        new_search = service.invoke(
            "knowledge.search",
            {
                "task_id": second["task"]["id"],
                "snapshot_id": second["turn"]["knowledge_snapshot_id"],
                "query": "New fact only",
                "limit": 10,
            },
        )
        graph = service.invoke("knowledge.graph.get", {"project_id": project["id"]})
        overview = service.invoke(
            "knowledge.projects.overview",
            {"project_id": project["id"]},
        )

        assert old_search["items"] == []
        assert len(new_search["items"]) == 1
        assert overview["obsidian_connected"] is True
        assert overview["obsidian_health"] == "ready"
        assert overview["watermark"] == graph["watermark"]
        assert any(node["source_id"] == source["id"] for node in graph["nodes"])
    finally:
        service.close()


def test_knowledge_rename_and_delete_preserve_old_snapshot_and_emit_events(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    original = vault / "Architecture.md"
    removed = vault / "Remove me.md"
    original.write_text("# Architecture\nStable content.", encoding="utf-8")
    removed.write_text("# Remove me\nHistorical content.", encoding="utf-8")
    data_dir = tmp_path / "data"
    path_token = ObsidianPathRegistry(data_dir / "obsidian-paths.json").register(vault)
    service = build_local_service(data_dir)
    try:
        context = service.invoke(
            "projects.create",
            {"name": "Revision project", "residency": "local_only"},
        )
        project = context["project"]
        conversation = service.invoke(
            "conversations.create",
            {"project_id": project["id"], "workspace_type": "project_chat"},
        )
        source = service.invoke(
            "obsidian.sources.create",
            {
                "project_id": project["id"],
                "display_name": "Revision Vault",
                "local_path_token": path_token,
                "read_scope": "whole_vault",
                "allowed_directories": [],
                "whole_vault_confirmed": True,
                "managed_directory": "Fairy",
                "mode": "read_only",
                "idempotency_key": "source:revision-vault",
            },
        )
        first_sync = service.invoke(
            "obsidian.sync.start",
            {"source_id": source["id"], "expected_revision": source["revision"]},
        )
        first = _task_and_turn(service, conversation["id"], "before-rename")
        first_snapshot = service.invoke(
            "knowledge.snapshots.get",
            {
                "task_id": first["task"]["id"],
                "snapshot_id": first["turn"]["knowledge_snapshot_id"],
            },
        )

        original.rename(vault / "System design.md")
        removed.unlink()
        second_sync = service.invoke(
            "obsidian.sync.start",
            {
                "source_id": source["id"],
                "expected_revision": first_sync["source"]["revision"],
            },
        )
        second = _task_and_turn(service, conversation["id"], "after-rename")
        second_snapshot = service.invoke(
            "knowledge.snapshots.get",
            {
                "task_id": second["task"]["id"],
                "snapshot_id": second["turn"]["knowledge_snapshot_id"],
            },
        )

        first_architecture = next(
            item for item in first_snapshot["items"] if item["relative_path"] == "Architecture.md"
        )
        renamed_architecture = next(
            item for item in second_snapshot["items"] if item["relative_path"] == "System design.md"
        )
        historical = service.invoke(
            "knowledge.read",
            {
                "task_id": first["task"]["id"],
                "snapshot_id": first_snapshot["id"],
                "revision_id": first_architecture["revision_id"],
            },
        )
        events = service.invoke("events.list", {"cursor": 0, "limit": 500})["items"]
        event_types = [event["event_type"] for event in events]

        assert second_sync["changed_count"] == 1
        assert second_sync["deleted_count"] == 1
        assert renamed_architecture["item_id"] == first_architecture["item_id"]
        assert {item["relative_path"] for item in first_snapshot["items"]} == {
            "Architecture.md",
            "Remove me.md",
        }
        assert {item["relative_path"] for item in second_snapshot["items"]} == {"System design.md"}
        assert historical["content"].splitlines() == ["# Architecture", "Stable content."]
        assert event_types.count("knowledge.sync.started") == 2
        assert event_types.count("knowledge.sync.completed") == 2
        assert "knowledge.item.revision.created" in event_types
        assert "knowledge.item.revision.deleted" in event_types
        assert event_types.count("knowledge.snapshot.created") == 2
        assert event_types.count("harness.manifest.created") == 2
        assert str(vault) not in json.dumps(events)
    finally:
        service.close()


def test_turn_context_uses_immutable_manifest_tools_after_registry_change(
    tmp_path: Path,
) -> None:
    service = build_local_service(tmp_path / "data")
    try:
        conversation = service.invoke(
            "conversations.create",
            {"project_id": None, "workspace_type": "chat_scratch"},
        )
        context = _task_and_turn(service, conversation["id"], "tool-snapshot")
        turn = service._assistant_ledger.get_turn(UUID(context["turn"]["id"]))
        capabilities = frozenset({ProviderCapability.TEXT, ProviderCapability.TOOLS})

        before = service._assistant_application._context.build(
            turn,
            provider_capabilities=capabilities,
        )
        service._registry.replace_namespace("artifact.", ())
        after = service._assistant_application._context.build(
            turn,
            provider_capabilities=capabilities,
        )

        before_names = tuple(definition.name for definition in before.tool_definitions)
        after_names = tuple(definition.name for definition in after.tool_definitions)
        assert "artifact.list" in before_names
        assert after_names == before_names
    finally:
        service.close()


def _task_and_turn(service, conversation_id: str, label: str) -> dict[str, dict]:
    task = service.invoke(
        "tasks.create",
        {
            "conversation_id": conversation_id,
            "user_request": f"Use project knowledge {label}",
            "operation_mode": "answer",
            "execution_target": "local",
            "idempotency_key": f"task:knowledge:{label}",
        },
    )["task"]
    turn = service.invoke(
        "assistant.turns.create",
        {
            "task_id": task["id"],
            "profile_id": "local-default",
            "idempotency_key": f"turn:knowledge:{label}",
        },
    )
    return {"task": task, "turn": turn}
