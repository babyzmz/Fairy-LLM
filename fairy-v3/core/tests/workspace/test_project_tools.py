from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from fairy_core.domain.execution import Artifact, ArtifactType, ArtifactVisibility
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider, wait_for_turn
from tests.runtime_support import FakeRuntimeExecutor


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


def _scratch_turn(service, *, key: str) -> tuple[dict[str, object], dict[str, object]]:
    conversation = service.invoke(
        "conversations.create",
        {"project_id": None, "workspace_type": "chat_scratch"},
    )
    context = service.invoke(
        "tasks.create",
        {
            "conversation_id": conversation["id"],
            "user_request": "Create a small multi-file webpage",
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
                        '"batch":1}],'
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
        waiting = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        approvals = service.invoke(
            "approvals.list",
            {"task_id": context["task"]["id"]},
        )["items"]

        assert waiting["status"] == "waiting_for_tool"
        assert len(approvals) == 1
        assert approvals[0]["changeset_id"] is not None
        assert approvals[0]["tool_invocation_id"] is None
        running_plan = service.invoke(
            "execution_plans.get",
            {"task_id": context["task"]["id"]},
        )
        assert running_plan["plan"]["model_calls_used"] == 2
        assert running_plan["plan"]["tool_calls_used"] == 2
        assert running_plan["plan"]["manifest"]["files"][0]["expected_hash"] == base_hash
        assert [
            step["status"] for step in running_plan["steps"] if step["kind"] == "implement"
        ] == ["running"]
        managed_file = Path(context["target_version"]["project_root"]) / "README.md"
        assert managed_file.read_text(encoding="utf-8") == "base"
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            before = unit_of_work.project_indexes.get(context["target_version"]["id"])
        assert before is not None and before.generation == 1

        decision = service.invoke(
            "approvals.decide",
            {"approval_id": approvals[0]["id"], "approved": True},
        )
        completed = wait_for_turn(service, turn["id"])

        assert decision["assistant_turn_id"] == turn["id"]
        assert decision["resume_requested"] is True
        assert completed["status"] == "completed"
        assert managed_file.read_text(encoding="utf-8") == "draft"
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            after = unit_of_work.project_indexes.get(context["target_version"]["id"])
        assert after is not None and after.generation == 2
        assert after.file("README.md").content_hash == hashlib.sha256(b"draft").hexdigest()
        completed_plan = service.invoke(
            "execution_plans.get",
            {"task_id": context["task"]["id"]},
        )
        assert completed_plan["plan"]["model_calls_used"] == 3
        assert completed_plan["plan"]["status"] == "active"
        assert [
            step["status"] for step in completed_plan["steps"] if step["kind"] == "implement"
        ] == ["completed"]
        assert (
            next(step["status"] for step in completed_plan["steps"] if step["kind"] == "checkpoint")
            == "pending"
        )

        service.invoke("tasks.review", {"task_id": context["task"]["id"]})
        reviewed_plan = service.invoke(
            "execution_plans.get",
            {"task_id": context["task"]["id"]},
        )
        assert reviewed_plan["plan"]["status"] == "completed"
        assert all(step["status"] in {"completed", "skipped"} for step in reviewed_plan["steps"])
    finally:
        service.close()


def test_scratch_edit_accepts_zero_hash_for_new_files_and_applies_complete_batch(
    tmp_path: Path,
) -> None:
    zero_hash = "0" * 64
    planned_files = [
        {
            "path": "index.html",
            "purpose": "Page structure",
            "batch": 1,
            "expected_hash": zero_hash,
        },
        {
            "path": "styles.css",
            "purpose": "Visual design",
            "batch": 1,
            "expected_hash": zero_hash,
        },
        {
            "path": "main.js",
            "purpose": "Animation behavior",
            "batch": 1,
            "expected_hash": zero_hash,
        },
    ]
    generated_files = [
        {"path": "index.html", "content": "<main>Fairy</main>"},
        {"path": "styles.css", "content": "main { color: teal; }"},
        {"path": "main.js", "content": "document.body.dataset.ready = 'true';"},
    ]
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-plan",
                    tool_name="execution.plan",
                    arguments_fragment=json.dumps(
                        {"files": planned_files, "entrypoints": ["index.html"]}
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
                    arguments_fragment=json.dumps(
                        {"files": generated_files, "reason": "Create the requested webpage"}
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
                    text="The webpage files are ready.",
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
        runtime_executor=FakeRuntimeExecutor(),
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        service.invoke(
            "permissions.update",
            {
                "profile": "autonomous",
                "capability_overrides": {},
                "expected_revision": 0,
                "idempotency_key": "permissions:zero-hash",
            },
        )
        context, turn = _scratch_turn(service, key="zero-hash")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        plan = service.invoke(
            "execution_plans.get",
            {"task_id": context["task"]["id"]},
        )
        preview = service.invoke(
            "previews.resolve",
            {
                "task_id": context["task"]["id"],
                "workspace_id": context["task"]["workspace_id"],
                "version_id": context["target_version"]["id"],
            },
        )
        files = service.invoke(
            "workspaces.files.list",
            {
                "workspace_id": context["task"]["workspace_id"],
                "version_id": context["target_version"]["id"],
            },
        )
        workspace = service.invoke(
            "workspaces.get",
            {"workspace_id": context["task"]["workspace_id"]},
        )
        completed_task = service.invoke("tasks.get", {"task_id": context["task"]["id"]})
        trace = service.invoke("assistant.turns.trace.list", {"turn_id": turn["id"]})
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            events = unit_of_work.commands.events_after(
                cursor=0,
                allowed_visibilities=None,
            )
        root = Path(context["target_version"]["project_root"])

        assert completed["status"] == "completed"
        assert [item["expected_hash"] for item in plan["plan"]["manifest"]["files"]] == [
            None,
            None,
            None,
        ]
        assert (root / "index.html").read_text(encoding="utf-8") == "<main>Fairy</main>"
        assert (root / "styles.css").read_text(encoding="utf-8") == ("main { color: teal; }")
        assert (root / "main.js").read_text(encoding="utf-8") == (
            "document.body.dataset.ready = 'true';"
        )
        assert {item["path"] for item in files["items"]} == {
            "index.html",
            "main.js",
            "styles.css",
        }
        assert preview["preview"]["status"] == "ready"
        assert preview["preview"]["version_id"] == context["target_version"]["id"]
        assert workspace["active_version_id"] == context["target_version"]["id"]
        assert completed_task["status"] == "ready"
        checkpoint_cursor = next(
            event.cursor
            for event in events
            if event.event_type == "command.succeeded"
            and event.payload.get("command_name") == "workspace.checkpoint"
        )
        promotion_cursor = next(
            event.cursor
            for event in events
            if event.event_type == "workspace.version.auto_promoted"
        )
        assert checkpoint_cursor < promotion_cursor
        assert any(
            step["kind"] == "verification"
            and step["status"] == "succeeded"
            and step["public_summary"] == "Preview ready"
            and len(step["artifact_refs"]) == 1
            for step in trace["steps"]
        )
        step_statuses = {item["kind"]: item["status"] for item in plan["steps"]}
        assert step_statuses == {
            "analyze": "completed",
            "file_plan": "completed",
            "implement": "completed",
            "install": "skipped",
            "test": "skipped",
            "preview": "completed",
            "repair": "skipped",
            "summary": "completed",
            "checkpoint": "completed",
        }
    finally:
        service.close()


def test_final_response_retries_when_plan_has_no_changeset(tmp_path: Path) -> None:
    plan_arguments = json.dumps(
        {
            "files": [
                {
                    "path": "index.html",
                    "purpose": "Page structure",
                    "batch": 1,
                    "expected_hash": None,
                }
            ],
            "entrypoints": ["index.html"],
        }
    )
    edit_arguments = json.dumps(
        {
            "files": [{"path": "index.html", "content": "<h1>Recovered</h1>"}],
            "reason": "Finish the planned batch",
        }
    )
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-plan",
                    tool_name="execution.plan",
                    arguments_fragment=plan_arguments,
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
                    text="The file is ready even though I did not create it.",
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-edit",
                    tool_name="edit.propose_changeset",
                    arguments_fragment=edit_arguments,
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
                    text="The durable webpage file is now ready.",
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
        runtime_executor=FakeRuntimeExecutor(),
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        service.invoke(
            "permissions.update",
            {
                "profile": "autonomous",
                "capability_overrides": {},
                "expected_revision": 0,
                "idempotency_key": "permissions:completion-retry",
            },
        )
        context, turn = _scratch_turn(service, key="completion-retry")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        messages = service.invoke(
            "messages.list",
            {"conversation_id": context["task"]["conversation_id"]},
        )["items"]
        events = service.invoke("events.subscribe", {"cursor": 0})["items"]
        trace = service.invoke("assistant.turns.trace.list", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        assert len(provider.requests) == 4
        assert any(
            message.role.value == "system"
            and "has no durable pending or applied Changeset" in message.content
            for message in provider.requests[2].messages
        )
        assert [item["content"] for item in messages if item["role"] == "assistant"] == [
            "The durable webpage file is now ready."
        ]
        assert any(
            event["event_type"] == "assistant.message.projection_reset"
            and event["payload"]["reason"] == "execution_incomplete"
            for event in events
        )
        assert any(
            step["kind"] == "model"
            and step["status"] == "failed"
            and step["public_summary"] == "Finalizing durable result"
            for step in trace["steps"]
        )
        root = Path(context["target_version"]["project_root"])
        assert (root / "index.html").read_text(encoding="utf-8") == ("<h1>Recovered</h1>")
    finally:
        service.close()


def test_generated_workspace_retries_a_code_dump_as_a_concise_summary(
    tmp_path: Path,
) -> None:
    plan_arguments = json.dumps(
        {
            "files": [
                {
                    "path": "index.html",
                    "purpose": "Page structure",
                    "batch": 1,
                    "expected_hash": None,
                }
            ],
            "entrypoints": ["index.html"],
        }
    )
    edit_arguments = json.dumps(
        {
            "files": [{"path": "index.html", "content": "<h1>Concise</h1>"}],
            "reason": "Create the planned webpage",
        }
    )
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-plan",
                    tool_name="execution.plan",
                    arguments_fragment=plan_arguments,
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
                    arguments_fragment=edit_arguments,
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
                    text=f"```html\n{'x' * 3_000}\n```",
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
            (
                ModelDelta.text(
                    profile_id="scripted",
                    sequence=1,
                    text=(
                        "Created index.html and verified it in the Workspace Preview. "
                        "The page is ready for review."
                    ),
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
        runtime_executor=FakeRuntimeExecutor(),
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        service.invoke(
            "permissions.update",
            {
                "profile": "autonomous",
                "capability_overrides": {},
                "expected_revision": 0,
                "idempotency_key": "permissions:concise-summary",
            },
        )
        context, turn = _scratch_turn(service, key="concise-summary")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        messages = service.invoke(
            "messages.list",
            {"conversation_id": context["task"]["conversation_id"]},
        )["items"]
        preview = service.invoke(
            "previews.resolve",
            {
                "task_id": context["task"]["id"],
                "workspace_id": context["task"]["workspace_id"],
                "version_id": context["target_version"]["id"],
            },
        )

        assert completed["status"] == "completed"
        assert len(provider.requests) == 4
        assert any(
            message.role.value == "system"
            and "candidate final response repeats generated source" in message.content
            for message in provider.requests[3].messages
        )
        assert [item["content"] for item in messages if item["role"] == "assistant"] == [
            "Created index.html and verified it in the Workspace Preview. "
            "The page is ready for review."
        ]
        assert preview["preview"]["status"] == "ready"
    finally:
        service.close()


def test_completed_file_batch_hands_preview_finalization_back_to_core(tmp_path: Path) -> None:
    plan_arguments = json.dumps(
        {
            "files": [
                {
                    "path": "index.html",
                    "purpose": "Static page",
                    "batch": 1,
                    "expected_hash": None,
                }
            ],
            "entrypoints": ["index.html"],
        }
    )
    edit_arguments = json.dumps(
        {
            "files": [{"path": "index.html", "content": "<h1>Ready</h1>"}],
            "reason": "Create the planned static page",
        }
    )
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-plan",
                    tool_name="execution.plan",
                    arguments_fragment=plan_arguments,
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
                    arguments_fragment=edit_arguments,
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
                    text="Created index.html; the Workspace Preview is ready.",
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
        runtime_executor=FakeRuntimeExecutor(),
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        service.invoke(
            "permissions.update",
            {
                "profile": "autonomous",
                "capability_overrides": {},
                "expected_revision": 0,
                "idempotency_key": "permissions:preview-status-prepares",
            },
        )
        context, turn = _scratch_turn(service, key="preview-status-prepares")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        preview = service.invoke(
            "previews.resolve",
            {
                "task_id": context["task"]["id"],
                "workspace_id": context["task"]["workspace_id"],
                "version_id": context["target_version"]["id"],
            },
        )
        execution_plan = service.invoke(
            "execution_plans.get",
            {"task_id": context["task"]["id"]},
        )

        assert completed["status"] == "completed"
        assert preview["preview"]["status"] == "ready"
        assert execution_plan["plan"]["status"] == "completed"
        assert "execution.plan" not in {tool.name for tool in provider.requests[1].tools}
        assert [tool.name for tool in provider.requests[2].tools] == ["direct_answer"]
        assert "CORE FINALIZATION HANDOFF" in provider.requests[2].messages[0].content
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
