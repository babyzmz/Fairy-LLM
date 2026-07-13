from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from fairy_core.domain.execution import Artifact, ArtifactType, ArtifactVisibility
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider


def _provider(tool_name: str, arguments: str, final_text: str) -> ScriptedProvider:
    return ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id=f"call-{tool_name}",
                    tool_name=tool_name,
                    arguments_fragment=arguments,
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="tool_calls",
                ),
            ),
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text=final_text),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ]
    )


def _project_turn(
    service, source: Path, *, key: str
) -> tuple[dict[str, object], dict[str, object]]:
    project = service.invoke(
        "projects.import",
        {"name": "Tool project", "residency": "local_only", "source_path": str(source)},
    )
    conversation = service.invoke(
        "conversations.create",
        {"project_id": project["project"]["id"], "workspace_type": "project_chat"},
    )
    context = service.invoke(
        "tasks.create",
        {
            "conversation_id": conversation["id"],
            "user_request": "Use a governed project tool",
            "operation_mode": "continue_current_chat_draft",
            "execution_target": "local",
            "idempotency_key": f"task:{key}",
        },
    )
    turn = service.invoke(
        "assistant.turns.create",
        {
            "task_id": context["task"]["id"],
            "profile_id": "scripted",
            "idempotency_key": f"turn:{key}",
        },
    )
    return context, turn


def test_project_read_uses_the_persisted_task_workspace(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "src" / "main.py").write_text("VALUE = 'managed'\n", encoding="utf-8")
    provider = _provider("project.read", '{"path":"src/main.py"}', "Read complete")
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        _context, turn = _project_turn(service, source, key="read")
        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        tool_result = provider.requests[1].messages[-1]
        assert tool_result.tool_call_id == "call-project.read"
        assert "VALUE = 'managed'" in tool_result.content
    finally:
        service.close()


def test_project_read_rejects_path_escape_without_disclosing_the_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("inside", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("OUTSIDE_SECRET", encoding="utf-8")
    provider = _provider("project.read", '{"path":"../../secret.txt"}', "Read rejected")
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        _context, turn = _project_turn(service, source, key="escape")
        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        tool_result = provider.requests[1].messages[-1]
        assert "rejected" in tool_result.content.lower()
        assert "OUTSIDE_SECRET" not in tool_result.content
    finally:
        service.close()


def test_project_read_treats_ignored_secrets_as_out_of_scope(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / ".env").write_text("TOKEN=DO_NOT_DISCLOSE", encoding="utf-8")
    provider = _provider("project.read", '{"path":".env"}', "Secret read rejected")
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        _context, turn = _project_turn(service, source, key="secret")
        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        tool_result = provider.requests[1].messages[-1]
        assert "PATH_OUT_OF_SCOPE" in tool_result.content
        assert "DO_NOT_DISCLOSE" not in tool_result.content
    finally:
        service.close()


def test_project_read_rejects_a_replaced_symlink_without_disclosing_target(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("inside", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("SYMLINK_SECRET", encoding="utf-8")
    provider = _provider("project.read", '{"path":"README.md"}', "Read rejected")
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        context, turn = _project_turn(service, source, key="symlink")
        managed_file = Path(context["target_version"]["project_root"]) / "README.md"
        managed_file.unlink()
        try:
            managed_file.symlink_to(secret)
        except OSError:
            pytest.skip("symlink creation is unavailable")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        tool_result = provider.requests[1].messages[-1]
        assert "rejected" in tool_result.content.lower()
        assert "SYMLINK_SECRET" not in tool_result.content
    finally:
        service.close()


def test_edit_tool_creates_a_changeset_approval_without_writing_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("base", encoding="utf-8")
    base_hash = hashlib.sha256(b"base").hexdigest()
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-plan",
                    tool_name="execution.plan",
                    arguments_fragment=(
                        '{"files":[{"path":"README.md","purpose":"Update copy",'
                        f'"batch":1,"expected_hash":"{base_hash}"}}],'
                        '"validation_commands":[]}'
                    ),
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="tool_calls",
                ),
            ),
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-edit",
                    tool_name="edit.propose_changeset",
                    arguments_fragment=(
                        '{"files":[{"path":"README.md","content":"draft"}],"reason":"Update copy"}'
                    ),
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="tool_calls",
                ),
            ),
            (
                ModelDelta.text(
                    profile_id="scripted",
                    sequence=1,
                    text="Draft is ready for approval",
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ]
    )
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        context, turn = _project_turn(service, source, key="changeset")
        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        approvals = service.invoke(
            "approvals.list",
            {"task_id": context["task"]["id"]},
        )["items"]

        assert completed["status"] == "completed"
        assert len(approvals) == 1
        assert approvals[0]["changeset_id"] is not None
        assert approvals[0]["tool_invocation_id"] is None
        running_plan = service.invoke(
            "execution_plans.get",
            {"task_id": context["task"]["id"]},
        )
        assert running_plan["plan"]["model_calls_used"] == 3
        assert running_plan["plan"]["tool_calls_used"] == 2
        assert [
            step["status"] for step in running_plan["steps"] if step["kind"] == "implement"
        ] == ["running"]
        managed_file = Path(context["target_version"]["project_root"]) / "README.md"
        assert managed_file.read_text(encoding="utf-8") == "base"
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            before = unit_of_work.project_indexes.get(context["target_version"]["id"])
        assert before is not None and before.generation == 1

        service.invoke(
            "approvals.decide",
            {"approval_id": approvals[0]["id"], "approved": True},
        )

        assert managed_file.read_text(encoding="utf-8") == "draft"
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            after = unit_of_work.project_indexes.get(context["target_version"]["id"])
        assert after is not None and after.generation == 2
        assert after.file("README.md").content_hash == hashlib.sha256(b"draft").hexdigest()
        completed_plan = service.invoke(
            "execution_plans.get",
            {"task_id": context["task"]["id"]},
        )
        assert [
            step["status"] for step in completed_plan["steps"] if step["kind"] == "implement"
        ] == ["completed"]
    finally:
        service.close()


def test_artifact_and_preview_tools_use_only_the_current_task_scope(tmp_path: Path) -> None:
    artifact_id = UUID("0198f4de-0114-7000-8000-000000000041")
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-artifact-list",
                    tool_name="artifact.list",
                    arguments_fragment="{}",
                ),
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=2,
                    tool_call_id="call-artifact-read",
                    tool_name="artifact.read",
                    arguments_fragment=f'{{"artifact_id":"{artifact_id}"}}',
                ),
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=3,
                    tool_call_id="call-preview-status",
                    tool_name="preview.status",
                    arguments_fragment="{}",
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=4,
                    finish_reason="tool_calls",
                ),
            ),
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text="Context checked"),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ]
    )
    source = tmp_path / "source"
    source.mkdir()
    content = "Durable artifact content"
    service = build_local_service(
        tmp_path / "data",
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        context, turn = _project_turn(service, source, key="artifact-preview")
        task = context["task"]
        version = context["target_version"]
        artifact = Artifact.restore(
            id=artifact_id,
            project_id=UUID(task["project_id"]),
            conversation_id=UUID(task["conversation_id"]),
            task_id=UUID(task["id"]),
            version_id=UUID(version["id"]),
            artifact_type=ArtifactType.REPORT,
            visibility=ArtifactVisibility.CONVERSATION,
            storage_location="inline://tests/artifact.md",
            media_type="text/markdown",
            byte_length=len(content.encode("utf-8")),
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            metadata={"content": content},
            created_at=datetime.now(UTC),
        )
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            unit_of_work.state.append_artifact(artifact)
            unit_of_work.commit()

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        results = {
            message.tool_call_id: message.content
            for message in provider.requests[1].messages
            if message.tool_call_id is not None
        }
        assert str(artifact_id) in results["call-artifact-list"]
        assert content in results["call-artifact-read"]
        assert '"runtime":null' in results["call-preview-status"]
        assert '"preview":null' in results["call-preview-status"]
    finally:
        service.close()


def test_project_tool_schemas_are_closed_and_scope_free(tmp_path: Path) -> None:
    service = build_local_service(tmp_path / "data")
    try:
        for name in (
            "project.read",
            "artifact.list",
            "artifact.read",
            "edit.propose_changeset",
            "execution.plan",
            "preview.status",
        ):
            definition = service._registry.get(name)  # type: ignore[attr-defined]
            assert definition is not None
            assert definition.input_schema["additionalProperties"] is False
            properties = definition.input_schema.get("properties", {})
            assert not {
                "project_id",
                "conversation_id",
                "task_id",
                "version_id",
                "project_root",
                "scope_digest",
            }.intersection(properties)
    finally:
        service.close()
