from __future__ import annotations

import json
from base64 import b64encode
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from fairy_core.assistant.tools import ToolExecutionUnavailableError
from fairy_core.browser import BrowserService, BrowserToolExecutor
from fairy_core.commanding.registry import ApprovalPolicy, SideEffect, build_default_registry
from fairy_core.contracts.browser import (
    BrowserActionInput,
    BrowserSessionListInput,
    BrowserSessionModel,
    BrowserSessionStartInput,
    BrowserSessionStatus,
    BrowserSnapshotInput,
    BrowserTabModel,
)
from fairy_core.contracts.common import ExecutionTarget
from fairy_core.domain.models import OperationMode, ScopeContract, WorkspaceType
from fairy_core.workspace.worker_transport import WorkerRpcError

_ONE_PIXEL_PNG = b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02"
        "0000000b4944415478da6364f80f00010501012718e3660000000049454e44ae426082"
    )
).decode("ascii")


class FakeBrowserWorker:
    def __init__(self) -> None:
        self.tab_id = uuid4()
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.calls.append((method, params))
        session_id = str(params.get("session_id", uuid4()))
        if method == "browser.tabs.open":
            self.tab_id = uuid4()
        if method == "browser.health":
            return {
                "available": True,
                "browser_name": "Microsoft Edge",
                "browser_version": "1",
                "error_code": None,
                "diagnostic": None,
            }
        if method == "browser.snapshots.get":
            include_screenshot = params.get("include_screenshot") is True
            return {
                "session_id": session_id,
                "tab_id": str(self.tab_id),
                "page_revision": 2,
                "url": "about:blank",
                "title": "New tab",
                "aria_snapshot": "- document",
                "viewport_width": 1 if include_screenshot else 1365,
                "viewport_height": 1 if include_screenshot else 768,
                "screenshot_data_url": (
                    f"data:image/png;base64,{_ONE_PIXEL_PNG}" if include_screenshot else None
                ),
                "captured_at": datetime.now(UTC).isoformat(),
            }
        status = "stopped" if method == "browser.sessions.stop" else "active"
        session = {
            "status": status,
            "active_tab_id": None if status == "stopped" else str(self.tab_id),
            "tabs": []
            if status == "stopped"
            else [
                {
                    "id": str(self.tab_id),
                    "session_id": session_id,
                    "title": "New tab",
                    "url": "about:blank",
                    "active": True,
                    "loading": False,
                    "revision": 2,
                }
            ],
        }
        if method == "browser.actions.execute":
            return {
                "session": session,
                "tab": session["tabs"][0],
                "public_summary": "Browser reload completed",
                "replayed": False,
            }
        return session


def service(tmp_path: Path, worker: FakeBrowserWorker | None = None) -> BrowserService:
    return BrowserService(
        worker=worker,
        state_path=tmp_path / "browser" / "sessions.json",
        profile_root=tmp_path / "browser" / "profiles",
    )


def test_browser_session_is_scoped_idempotent_and_recoverable(tmp_path: Path) -> None:
    worker = FakeBrowserWorker()
    browser = service(tmp_path, worker)
    conversation_id = uuid4()
    request = BrowserSessionStartInput(
        conversation_id=conversation_id,
        initial_url="about:blank",
        idempotency_key="browser-start",
    )

    started = browser.start(request)
    replayed = browser.start(request)

    assert started.id == replayed.id
    assert started.status is BrowserSessionStatus.ACTIVE
    assert browser.list(BrowserSessionListInput(conversation_id=conversation_id)).items == (
        started,
    )
    assert [name for name, _params in worker.calls].count("browser.sessions.start") == 1

    recovered = service(tmp_path).get(started.id)
    assert recovered.status is BrowserSessionStatus.INTERRUPTED


def test_browser_action_and_snapshot_preserve_page_revision(tmp_path: Path) -> None:
    worker = FakeBrowserWorker()
    browser = service(tmp_path, worker)
    session = browser.start(
        BrowserSessionStartInput(initial_url="about:blank", idempotency_key="browser-action")
    )
    tab_id = UUID(str(session.active_tab_id))

    action = browser.execute(
        BrowserActionInput(
            session_id=session.id,
            tab_id=tab_id,
            kind="reload",
            expected_page_revision=2,
            idempotency_key="reload-once",
        )
    )
    snapshot = browser.snapshot(
        BrowserSnapshotInput(session_id=session.id, tab_id=tab_id, include_screenshot=False)
    )

    assert action.public_summary == "Browser reload completed"
    assert snapshot.page_revision == 2
    assert snapshot.aria_snapshot == "- document"
    assert snapshot.viewport_width == 1365
    assert snapshot.viewport_height == 768
    assert browser.get(session.id).tabs[0].revision == snapshot.page_revision


def test_browser_rejects_credential_urls_before_worker_call(tmp_path: Path) -> None:
    worker = FakeBrowserWorker()
    browser = service(tmp_path, worker)

    with pytest.raises(ValueError, match="credentials"):
        browser.start(
            BrowserSessionStartInput(
                initial_url="https://user:secret@example.com/",
                idempotency_key="blocked-url",
            )
        )

    assert all(name != "browser.sessions.start" for name, _params in worker.calls)


def test_browser_tools_use_risk_based_approval() -> None:
    tools = {
        definition.name: definition
        for definition in build_default_registry().definitions()
        if definition.name.startswith("browser.")
    }

    assert tools["browser.snapshot"].side_effect is SideEffect.READ
    assert tools["browser.snapshot"].approval_policy is ApprovalPolicy.NEVER
    assert tools["browser.click"].side_effect is SideEffect.WRITE
    assert tools["browser.click"].approval_policy is ApprovalPolicy.PROFILE


def test_browser_session_list_can_require_an_exact_task_scope(tmp_path: Path) -> None:
    worker = FakeBrowserWorker()
    browser = service(tmp_path, worker)
    conversation_id = uuid4()
    task_id = uuid4()
    unscoped = browser.start(
        BrowserSessionStartInput(conversation_id=conversation_id, idempotency_key="unscoped")
    )
    scoped = browser.start(
        BrowserSessionStartInput(
            conversation_id=conversation_id,
            task_id=task_id,
            idempotency_key="scoped",
        )
    )

    assert browser.list(
        BrowserSessionListInput(
            conversation_id=conversation_id,
            task_id=None,
            exact_task_scope=True,
        )
    ).items == (unscoped,)
    assert browser.list(
        BrowserSessionListInput(
            conversation_id=conversation_id,
            task_id=task_id,
            exact_task_scope=True,
        )
    ).items == (scoped,)


def test_agent_refuses_an_interrupted_session_instead_of_replaying_it(tmp_path: Path) -> None:
    worker = FakeBrowserWorker()
    browser = service(tmp_path, worker)
    scope = _scope(tmp_path)
    browser.start(
        BrowserSessionStartInput(
            conversation_id=scope.conversation_id,
            task_id=scope.task_id,
            idempotency_key="agent-session",
        )
    )
    recovered = service(tmp_path, worker)

    with pytest.raises(ToolExecutionUnavailableError, match="resume"):
        recovered.session_for_scope(scope)


def test_agent_action_is_fenced_by_the_current_page_revision(tmp_path: Path) -> None:
    worker = FakeBrowserWorker()
    browser = service(tmp_path, worker)
    scope = _scope(tmp_path)
    definition = build_default_registry().get("browser.click")
    executor = BrowserToolExecutor(service=browser, delegate=None)

    executor.execute(definition, scope, {"selector": "button"})

    action = next(params for method, params in worker.calls if method == "browser.actions.execute")
    assert action["expected_page_revision"] == 2


def test_agent_can_set_viewport_and_receive_transient_visual_evidence(tmp_path: Path) -> None:
    worker = FakeBrowserWorker()
    browser = service(tmp_path, worker)
    scope = _scope(tmp_path)
    executor = BrowserToolExecutor(service=browser, delegate=None)

    viewport = executor.execute(
        build_default_registry().get("browser.viewport"),
        scope,
        {"width": 390, "height": 844},
    )
    snapshot = executor.execute(
        build_default_registry().get("browser.snapshot"),
        scope,
        {"include_screenshot": True, "capture_label": "mobile"},
    )

    action = next(params for method, params in worker.calls if method == "browser.actions.execute")
    assert action["kind"] == "viewport"
    assert (action["width"], action["height"]) == (390, 844)
    assert viewport.images == ()
    assert snapshot.public_summary == "Captured mobile Browser evidence"
    assert len(snapshot.images) == 1
    image = snapshot.images[0]
    assert image.media_type == "image/png"
    assert (image.width, image.height) == (1, 1)
    assert image.untrusted_data is True


def test_resume_recreates_all_tabs_and_restores_the_active_tab(tmp_path: Path) -> None:
    session_id = uuid4()
    first_tab_id = uuid4()
    second_tab_id = uuid4()
    now = datetime.now(UTC)
    session = BrowserSessionModel(
        id=session_id,
        conversation_id=uuid4(),
        task_id=uuid4(),
        execution_target=ExecutionTarget.LOCAL,
        profile_kind="persistent",
        status="interrupted",
        active_tab_id=first_tab_id,
        tabs=(
            BrowserTabModel(id=first_tab_id, session_id=session_id, url="about:blank"),
            BrowserTabModel(
                id=second_tab_id,
                session_id=session_id,
                title="Second",
                url="https://example.com/second",
                active=False,
            ),
        ),
        created_at=now,
        updated_at=now,
    )
    state_path = tmp_path / "browser" / "sessions.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps({"sessions": [session.model_dump(mode="json")], "idempotency": {}}),
        encoding="utf-8",
    )
    worker = FakeBrowserWorker()
    browser = service(tmp_path, worker)

    browser.resume(session_id)

    calls = worker.calls
    start_params = next(params for method, params in calls if method == "browser.sessions.start")
    open_params = next(params for method, params in calls if method == "browser.tabs.open")
    assert start_params["initial_url"] == "about:blank"
    assert open_params["url"] == "https://example.com/second"
    assert any(method == "browser.tabs.select" for method, _params in calls)


def test_worker_cannot_replace_a_session_with_foreign_tabs(tmp_path: Path) -> None:
    class ForeignTabWorker(FakeBrowserWorker):
        def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
            result = super().call(method, params)
            if method == "browser.sessions.start":
                assert isinstance(result.get("tabs"), list)
                result["tabs"][0]["session_id"] = str(uuid4())
            return result

    worker = ForeignTabWorker()
    browser = service(tmp_path, worker)

    session = browser.start(
        BrowserSessionStartInput(initial_url="about:blank", idempotency_key="foreign-tab")
    )

    assert session.status is BrowserSessionStatus.INTERRUPTED
    assert session.error_code == "WORKER_INTERRUPTED"


def test_worker_interruption_marks_an_active_session_before_recovery(tmp_path: Path) -> None:
    class InterruptedActionWorker(FakeBrowserWorker):
        def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
            if method == "browser.actions.execute":
                raise WorkerRpcError("worker exited")
            return super().call(method, params)

    worker = InterruptedActionWorker()
    browser = service(tmp_path, worker)
    session = browser.start(
        BrowserSessionStartInput(initial_url="about:blank", idempotency_key="interrupt-action")
    )

    with pytest.raises(WorkerRpcError, match="worker exited"):
        browser.execute(
            BrowserActionInput(
                session_id=session.id,
                tab_id=UUID(str(session.active_tab_id)),
                kind="reload",
                expected_page_revision=2,
                idempotency_key="interrupted-write",
            )
        )

    assert browser.get(session.id).status is BrowserSessionStatus.INTERRUPTED


def test_worker_generation_change_interrupts_every_old_active_session(tmp_path: Path) -> None:
    class GenerationWorker(FakeBrowserWorker):
        generation = 0

    worker = GenerationWorker()
    browser = service(tmp_path, worker)
    first = browser.start(BrowserSessionStartInput(idempotency_key="generation-one"))
    second = browser.start(BrowserSessionStartInput(idempotency_key="generation-two"))

    worker.generation = 1
    sessions = browser.list(BrowserSessionListInput()).items

    assert {session.id for session in sessions} == {first.id, second.id}
    assert all(session.status is BrowserSessionStatus.INTERRUPTED for session in sessions)


def _scope(tmp_path: Path) -> ScopeContract:
    return ScopeContract.create(
        workspace_type=WorkspaceType.CHAT_SCRATCH,
        project_id=None,
        conversation_id=uuid4(),
        task_id=uuid4(),
        operation_mode=OperationMode.ANSWER,
        base_version_id=None,
        target_version_id=None,
        project_root=tmp_path,
        allowed_write_paths=(tmp_path,),
        forbidden_write_paths=(),
        execution_target="local",
        network_policy="restricted",
        memory_read_scope=(),
        memory_write_scope=(),
    )
