from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import json
import socket
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fairy_core.assistant.evidence import (
    EvidenceDraft,
    EvidenceRequirementKind,
    EvidenceSourceKind,
)
from fairy_core.assistant.tools import (
    DelegatingToolCancellation,
    ToolExecutionUnavailableError,
    ToolExecutor,
    ToolResult,
)
from fairy_core.commanding.registry import ToolDefinition
from fairy_core.contracts.browser import (
    BrowserActionInput,
    BrowserActionResultModel,
    BrowserDownloadModel,
    BrowserProfileKind,
    BrowserProfileModel,
    BrowserSessionListInput,
    BrowserSessionModel,
    BrowserSessionPageModel,
    BrowserSessionStartInput,
    BrowserSessionStatus,
    BrowserSnapshotInput,
    BrowserSnapshotModel,
    BrowserTabIdInput,
    BrowserTabModel,
    BrowserTabOpenInput,
    BrowserWorkerHealthModel,
)
from fairy_core.domain.models import ScopeContract
from fairy_core.perception import MAX_IMAGE_BYTES, parse_png_dimensions
from fairy_core.providers import ModelImage


class BrowserWorker(Protocol):
    def call(self, method: str, params: dict[str, object]) -> dict[str, object]: ...


class BrowserService:
    def __init__(
        self,
        *,
        worker: BrowserWorker | None,
        state_path: Path,
        profile_root: Path,
    ) -> None:
        self._worker = worker
        self._state_path = state_path
        self._profile_root = profile_root
        self._lock = RLock()
        self._sessions: dict[UUID, BrowserSessionModel] = {}
        self._idempotency: dict[str, UUID] = {}
        self._worker_generation = self._current_worker_generation()
        self._load_interrupted_state()

    @property
    def handlers(self) -> Mapping[str, object]:
        return {
            "browser.health": lambda _request: self.health(),
            "browser.profile.get": lambda _request: BrowserProfileModel(),
            "browser.sessions.start": self.start,
            "browser.sessions.get": lambda request: self.get(request.session_id),
            "browser.sessions.list": self.list,
            "browser.sessions.stop": lambda request: self.stop(request.session_id),
            "browser.sessions.resume": lambda request: self.resume(request.session_id),
            "browser.tabs.open": self.open_tab,
            "browser.tabs.select": self.select_tab,
            "browser.tabs.close": self.close_tab,
            "browser.actions.execute": self.execute,
            "browser.snapshots.get": self.snapshot,
        }

    def health(self) -> BrowserWorkerHealthModel:
        if self._worker is None:
            return BrowserWorkerHealthModel(
                available=False,
                error_code="CAPABILITY_NOT_AVAILABLE",
                diagnostic="Fairy Browser Worker is not configured",
            )
        try:
            health = BrowserWorkerHealthModel.model_validate(
                self._worker.call("browser.health", {})
            )
            self._synchronize_worker_generation()
            return health
        except Exception as error:
            self._synchronize_worker_generation()
            return BrowserWorkerHealthModel(
                available=False,
                error_code="WORKER_INTERRUPTED",
                diagnostic=str(error)[:500],
            )

    def start(self, request: BrowserSessionStartInput) -> BrowserSessionModel:
        if request.execution_target.value != "local":
            raise ToolExecutionUnavailableError("Cloud browser sessions require an OCI worker")
        worker = self._required_worker()
        initial_url = _validated_url(request.initial_url or "about:blank")
        with self._lock:
            existing_id = self._idempotency.get(request.idempotency_key)
            if existing_id is not None:
                return self._sessions[existing_id]
            session_id = uuid4()
            now = datetime.now(UTC)
            session = BrowserSessionModel(
                id=session_id,
                project_id=request.project_id,
                conversation_id=request.conversation_id,
                task_id=request.task_id,
                execution_target=request.execution_target,
                profile_kind=request.profile_kind,
                status=BrowserSessionStatus.STARTING,
                created_at=now,
                updated_at=now,
            )
            self._sessions[session_id] = session
            self._idempotency[request.idempotency_key] = session_id
            self._save()
        try:
            result = worker.call(
                "browser.sessions.start",
                {
                    "session_id": str(session_id),
                    "profile_kind": request.profile_kind.value,
                    "profile_root": str(self._profile_root),
                    "initial_url": initial_url,
                },
            )
            return self._replace_from_worker(session_id, result)
        except Exception as error:
            return self._fail(session_id, error)

    def get(self, session_id: UUID) -> BrowserSessionModel:
        self._synchronize_worker_generation()
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Browser session not found: {session_id}")
        return session

    def list(self, request: BrowserSessionListInput) -> BrowserSessionPageModel:
        self._synchronize_worker_generation()
        terminal = {BrowserSessionStatus.STOPPED, BrowserSessionStatus.FAILED}
        with self._lock:
            items = tuple(
                session
                for session in self._sessions.values()
                if (
                    request.conversation_id is None
                    or session.conversation_id == request.conversation_id
                )
                and (
                    session.task_id == request.task_id
                    if request.exact_task_scope
                    else request.task_id is None or session.task_id == request.task_id
                )
                and (request.include_terminal or session.status not in terminal)
            )
        return BrowserSessionPageModel(
            items=tuple(sorted(items, key=lambda item: item.updated_at, reverse=True))
        )

    def stop(self, session_id: UUID) -> BrowserSessionModel:
        session = self.get(session_id)
        if session.status in {BrowserSessionStatus.STOPPED, BrowserSessionStatus.FAILED}:
            return session
        worker = self._required_worker()
        try:
            result = worker.call("browser.sessions.stop", {"session_id": str(session_id)})
            return self._replace_from_worker(session_id, result)
        except Exception as error:
            return self._fail(session_id, error)

    def resume(self, session_id: UUID) -> BrowserSessionModel:
        session = self.get(session_id)
        if session.status is BrowserSessionStatus.ACTIVE:
            return session
        if session.status not in {
            BrowserSessionStatus.INTERRUPTED,
            BrowserSessionStatus.SUSPENDED,
        }:
            raise ValueError("Only an interrupted or suspended Browser session can be resumed")
        original_tabs = session.tabs
        initial_url = original_tabs[0].url if original_tabs else "about:blank"
        request = BrowserSessionStartInput(
            project_id=session.project_id,
            conversation_id=session.conversation_id,
            task_id=session.task_id,
            execution_target=session.execution_target,
            profile_kind=session.profile_kind,
            initial_url=initial_url,
            idempotency_key=f"resume:{session.id}:{session.revision}",
        )
        resumed = self.start(request)
        restored_tab_ids = [resumed.active_tab_id]
        for tab in original_tabs[1:]:
            resumed = self.open_tab(BrowserTabOpenInput(session_id=resumed.id, url=tab.url))
            restored_tab_ids.append(resumed.active_tab_id)
        if session.active_tab_id is not None:
            active_index = next(
                (
                    index
                    for index, tab in enumerate(original_tabs)
                    if tab.id == session.active_tab_id
                ),
                0,
            )
            restored_id = restored_tab_ids[active_index] if restored_tab_ids else None
            if restored_id is not None and resumed.active_tab_id != restored_id:
                resumed = self.select_tab(
                    BrowserTabIdInput(session_id=resumed.id, tab_id=restored_id)
                )
        return resumed

    def open_tab(self, request: BrowserTabOpenInput) -> BrowserSessionModel:
        try:
            result = self._required_worker().call(
                "browser.tabs.open",
                {"session_id": str(request.session_id), "url": _validated_url(request.url)},
            )
            return self._replace_from_worker(request.session_id, result)
        except Exception as error:
            self._interrupt_on_worker_failure(request.session_id, error)
            raise

    def select_tab(self, request: BrowserTabIdInput) -> BrowserSessionModel:
        try:
            result = self._required_worker().call(
                "browser.tabs.select",
                {"session_id": str(request.session_id), "tab_id": str(request.tab_id)},
            )
            return self._replace_from_worker(request.session_id, result)
        except Exception as error:
            self._interrupt_on_worker_failure(request.session_id, error)
            raise

    def close_tab(self, request: BrowserTabIdInput) -> BrowserSessionModel:
        try:
            result = self._required_worker().call(
                "browser.tabs.close",
                {"session_id": str(request.session_id), "tab_id": str(request.tab_id)},
            )
            return self._replace_from_worker(request.session_id, result)
        except Exception as error:
            self._interrupt_on_worker_failure(request.session_id, error)
            raise

    def execute(self, request: BrowserActionInput) -> BrowserActionResultModel:
        try:
            params = request.model_dump(mode="json")
            if request.kind.value == "navigate":
                params["value"] = _validated_url(request.value or "")
            if request.kind.value == "download":
                session = self.get(request.session_id)
                if session.task_id is None:
                    raise ValueError("Browser downloads require a task-scoped session")
                download_root = self._download_root(session.task_id)
                download_root.mkdir(parents=True, exist_ok=True)
                params.update(
                    {
                        "task_id": str(session.task_id),
                        "download_dir": str(download_root),
                        "download_max_bytes": request.download_max_bytes or 50 * 1024 * 1024,
                    }
                )
            result = self._required_worker().call("browser.actions.execute", params)
            session = self._replace_from_worker(request.session_id, result["session"])
            tab = BrowserTabModel.model_validate(result["tab"])
            if tab.session_id != request.session_id or tab.id != request.tab_id:
                raise ValueError("Browser Worker returned an out-of-scope action tab")
            session_tab = next((item for item in session.tabs if item.id == tab.id), None)
            if session_tab is None:
                raise ValueError("Browser Worker action tab is absent from its session")
            if session_tab != tab:
                raise ValueError("Browser Worker returned inconsistent action tab state")
            download = None
            if result.get("download") is not None:
                download = BrowserDownloadModel.model_validate(result["download"])
                session = self._record_download(session, tab, download)
            return BrowserActionResultModel(
                session=session,
                tab=tab,
                public_summary=str(result.get("public_summary", "Browser action completed")),
                download=download,
                replayed=bool(result.get("replayed", False)),
            )
        except Exception as error:
            self._interrupt_on_worker_failure(request.session_id, error)
            raise

    def snapshot(self, request: BrowserSnapshotInput) -> BrowserSnapshotModel:
        try:
            result = self._required_worker().call(
                "browser.snapshots.get", request.model_dump(mode="json")
            )
            snapshot = BrowserSnapshotModel.model_validate(result)
            if snapshot.session_id != request.session_id or snapshot.tab_id != request.tab_id:
                raise ValueError("Browser Worker returned an out-of-scope snapshot")
            self._merge_snapshot(snapshot)
            return snapshot
        except Exception as error:
            self._interrupt_on_worker_failure(request.session_id, error)
            raise

    def session_for_scope(self, scope: ScopeContract) -> BrowserSessionModel:
        matches = self.list(
            BrowserSessionListInput(
                conversation_id=scope.conversation_id,
                task_id=scope.task_id,
                exact_task_scope=True,
            )
        ).items
        if matches:
            session = matches[0]
            if session.status is not BrowserSessionStatus.ACTIVE:
                raise ToolExecutionUnavailableError(
                    "Browser session was interrupted; resume it in Preview before continuing"
                )
            return session
        return self.start(
            BrowserSessionStartInput(
                project_id=scope.project_id,
                conversation_id=scope.conversation_id,
                task_id=scope.task_id,
                profile_kind=BrowserProfileKind.PERSISTENT,
                idempotency_key=f"assistant-browser:{scope.task_id}",
            )
        )

    def _replace_from_worker(self, session_id: UUID, raw: object) -> BrowserSessionModel:
        payload = dict(raw) if isinstance(raw, dict) else {}
        with self._lock:
            previous = self._sessions[session_id]
            tabs = tuple(BrowserTabModel.model_validate(item) for item in payload.get("tabs", ()))
            if any(tab.session_id != session_id for tab in tabs):
                raise ValueError("Browser Worker returned a tab from another session")
            active_tab_id = UUID(payload["active_tab_id"]) if payload.get("active_tab_id") else None
            if active_tab_id is not None and not any(tab.id == active_tab_id for tab in tabs):
                raise ValueError("Browser Worker returned an unknown active tab")
            if any(tab.active != (tab.id == active_tab_id) for tab in tabs):
                raise ValueError("Browser Worker returned inconsistent active tab state")
            session = previous.model_copy(
                update={
                    "status": BrowserSessionStatus(payload.get("status", "active")),
                    "active_tab_id": active_tab_id,
                    "tabs": tabs,
                    "revision": previous.revision + 1,
                    "updated_at": datetime.now(UTC),
                    "error_code": None,
                    "public_error": None,
                }
            )
            self._sessions[session_id] = session
            self._save()
            return session

    def _interrupt_on_worker_failure(self, session_id: UUID, error: Exception) -> None:
        error_code = getattr(error, "error_code", None)
        if error_code in {None, "WORKER_INTERRUPTED"}:
            self._fail(session_id, error)

    def _merge_snapshot(self, snapshot: BrowserSnapshotModel) -> None:
        with self._lock:
            previous = self._sessions[snapshot.session_id]
            current = next((tab for tab in previous.tabs if tab.id == snapshot.tab_id), None)
            if current is None:
                raise ValueError("Browser snapshot tab is absent from its session")
            if snapshot.page_revision < current.revision:
                raise ValueError("Browser Worker attempted to roll back a page revision")
            updated = current.model_copy(
                update={
                    "title": snapshot.title,
                    "url": snapshot.url,
                    "revision": snapshot.page_revision,
                }
            )
            if updated == current:
                return
            tabs = tuple(updated if tab.id == updated.id else tab for tab in previous.tabs)
            self._sessions[previous.id] = previous.model_copy(
                update={
                    "tabs": tabs,
                    "revision": previous.revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._save()

    def _download_root(self, task_id: UUID) -> Path:
        return (self._profile_root.parent / "downloads" / str(task_id)).resolve()

    def _record_download(
        self,
        session: BrowserSessionModel,
        tab: BrowserTabModel,
        download: BrowserDownloadModel,
    ) -> BrowserSessionModel:
        if session.task_id is None or download.task_id != session.task_id:
            raise ValueError("Browser Worker returned an out-of-scope download task")
        if download.session_id != session.id or download.tab_id != tab.id:
            raise ValueError("Browser Worker returned an out-of-scope download")
        root = self._download_root(session.task_id)
        local_path = Path(download.local_path).resolve()
        if not local_path.is_relative_to(root):
            raise ValueError("Browser Worker returned an out-of-scope download path")
        if not local_path.is_file():
            raise ValueError("Browser Worker download is absent")
        stat = local_path.stat()
        if stat.st_size != download.size_bytes or stat.st_size > 50 * 1024 * 1024:
            local_path.unlink(missing_ok=True)
            raise ValueError("Browser Worker download size is inconsistent")
        digest = hashlib.sha256()
        with local_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != download.sha256:
            local_path.unlink(missing_ok=True)
            raise ValueError("Browser Worker download hash is inconsistent")
        with self._lock:
            current = self._sessions[session.id]
            downloads = (
                *(item for item in current.downloads if item.id != download.id),
                download,
            )
            updated = current.model_copy(
                update={
                    "downloads": downloads[-100:],
                    "revision": current.revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._sessions[session.id] = updated
            self._save()
            return updated

    def _fail(self, session_id: UUID, error: Exception) -> BrowserSessionModel:
        with self._lock:
            previous = self._sessions[session_id]
            failed = previous.model_copy(
                update={
                    "status": BrowserSessionStatus.INTERRUPTED,
                    "revision": previous.revision + 1,
                    "updated_at": datetime.now(UTC),
                    "error_code": getattr(error, "error_code", "WORKER_INTERRUPTED"),
                    "public_error": str(error)[:500],
                }
            )
            self._sessions[session_id] = failed
            self._save()
            return failed

    def _required_worker(self) -> BrowserWorker:
        if self._worker is None:
            raise ToolExecutionUnavailableError("Fairy Browser Worker is unavailable")
        self._synchronize_worker_generation()
        return self._worker

    def _current_worker_generation(self) -> int:
        generation = getattr(self._worker, "generation", 0)
        return generation if isinstance(generation, int) else 0

    def _synchronize_worker_generation(self) -> None:
        current = self._current_worker_generation()
        now = datetime.now(UTC)
        changed = False
        with self._lock:
            if current <= self._worker_generation:
                return
            self._worker_generation = current
            for session_id, session in tuple(self._sessions.items()):
                if session.status not in {
                    BrowserSessionStatus.ACTIVE,
                    BrowserSessionStatus.STARTING,
                }:
                    continue
                self._sessions[session_id] = session.model_copy(
                    update={
                        "status": BrowserSessionStatus.INTERRUPTED,
                        "revision": session.revision + 1,
                        "updated_at": now,
                        "error_code": "WORKER_INTERRUPTED",
                        "public_error": "Browser Worker restarted; resume this session explicitly",
                    }
                )
                changed = True
            if changed:
                self._save()

    def _load_interrupted_state(self) -> None:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        cutoff = datetime.now(UTC) - timedelta(days=7)
        terminal = {BrowserSessionStatus.STOPPED, BrowserSessionStatus.FAILED}
        for item in payload.get("sessions", ()):
            try:
                session = BrowserSessionModel.model_validate(item)
            except ValueError:
                continue
            if session.status in {BrowserSessionStatus.ACTIVE, BrowserSessionStatus.STARTING}:
                session = session.model_copy(
                    update={
                        "status": BrowserSessionStatus.INTERRUPTED,
                        "updated_at": datetime.now(UTC),
                    }
                )
            if session.status in terminal and session.updated_at < cutoff:
                continue
            self._sessions[session.id] = session
        for key, raw_session_id in payload.get("idempotency", {}).items():
            try:
                session_id = UUID(raw_session_id)
            except (TypeError, ValueError):
                continue
            if isinstance(key, str) and session_id in self._sessions:
                self._idempotency[key] = session_id

    def _save(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "sessions": [item.model_dump(mode="json") for item in self._sessions.values()],
                    "idempotency": {
                        key: str(session_id) for key, session_id in self._idempotency.items()
                    },
                },
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        temporary.replace(self._state_path)


class BrowserToolExecutor(DelegatingToolCancellation):
    def __init__(self, *, service: BrowserService, delegate: ToolExecutor | None) -> None:
        self._service = service
        self._delegate = delegate

    def execute(
        self,
        definition: ToolDefinition,
        scope: ScopeContract,
        arguments: dict[str, object],
    ) -> ToolResult:
        if definition.executor != "browser_worker":
            if self._delegate is None:
                raise ToolExecutionUnavailableError(
                    f"tool executor is unavailable: {definition.executor}"
                )
            return self._delegate.execute(definition, scope, arguments)
        session = self._service.session_for_scope(scope)
        tab_id = session.active_tab_id
        if tab_id is None:
            raise ToolExecutionUnavailableError("Browser session has no active tab")
        if definition.name == "browser.snapshot":
            include_screenshot = arguments.get("include_screenshot") is True
            capture_label = arguments.get("capture_label")
            snapshot = self._service.snapshot(
                BrowserSnapshotInput(
                    session_id=session.id,
                    tab_id=tab_id,
                    include_screenshot=include_screenshot,
                )
            )
            images = (_model_image_from_snapshot(scope, snapshot),) if include_screenshot else ()
            summary = (
                f"Captured {capture_label} Browser evidence"
                if isinstance(capture_label, str)
                else "Inspected the current web page"
            )
            element_lines = "\n".join(
                (
                    f"[{element.ref}] role={element.role} tag={element.tag} "
                    f"name={json.dumps(element.name, ensure_ascii=False)}"
                )
                for element in snapshot.elements
            )
            element_section = (
                f"\nStable element references:\n{element_lines}" if element_lines else ""
            )
            return ToolResult.create(
                public_summary=summary,
                model_content=(
                    f"URL: {snapshot.url}\nTitle: {snapshot.title}\n"
                    f"Viewport: {snapshot.viewport_width}x{snapshot.viewport_height}\n"
                    f"{snapshot.aria_snapshot}{element_section}"
                ),
                artifact_ids=(),
                images=images,
                evidence_drafts=(
                    EvidenceDraft(
                        requirement_kind=EvidenceRequirementKind.RUNTIME_CURRENT,
                        source_kind=EvidenceSourceKind.RUNTIME_SNAPSHOT,
                        public_label=snapshot.title or "Current Browser page",
                        content_hash=hashlib.sha256(
                            (f"{snapshot.url}\n{snapshot.title}\n{snapshot.aria_snapshot}").encode()
                        ).hexdigest(),
                        source_revision=(
                            f"browser:{snapshot.session_id}:{snapshot.tab_id}:"
                            f"{snapshot.page_revision}"
                        ),
                        observed_at=snapshot.captured_at,
                        expires_at=snapshot.captured_at + timedelta(seconds=30),
                    ),
                ),
            )
        if definition.name == "browser.tab":
            action = arguments.get("action")
            if action == "open":
                raw_url = arguments.get("url", "about:blank")
                if not isinstance(raw_url, str):
                    raise ValueError("Browser tab URL must be a string")
                session = self._service.open_tab(
                    BrowserTabOpenInput(session_id=session.id, url=raw_url)
                )
            elif action in {"select", "close"}:
                raw_tab_id = arguments.get("tab_id")
                if not isinstance(raw_tab_id, str):
                    raise ValueError(f"Browser tab {action} requires tab_id")
                request = BrowserTabIdInput(session_id=session.id, tab_id=UUID(raw_tab_id))
                session = (
                    self._service.select_tab(request)
                    if action == "select"
                    else self._service.close_tab(request)
                )
            else:
                raise ValueError("Browser tab action must be open, select, or close")
            tabs = "\n".join(
                f"{item.id} {'active' if item.active else 'inactive'} {item.title}: {item.url}"
                for item in session.tabs
            )
            return ToolResult.create(
                public_summary=f"Browser tab {action} completed",
                model_content=f"Browser tab {action} completed\n{tabs}",
                artifact_ids=(),
            )
        kind = {
            "browser.back": "go_back",
            "browser.forward": "go_forward",
        }.get(definition.name, definition.name.removeprefix("browser."))
        result = self._service.execute(
            BrowserActionInput(
                session_id=session.id,
                tab_id=tab_id,
                kind=kind,
                element_ref=arguments.get("element_ref")
                if isinstance(arguments.get("element_ref"), str)
                else None,
                selector=arguments.get("selector")
                if isinstance(arguments.get("selector"), str)
                else None,
                value=arguments.get("value") if isinstance(arguments.get("value"), str) else None,
                x=float(arguments["x"]) if isinstance(arguments.get("x"), (int, float)) else None,
                y=float(arguments["y"]) if isinstance(arguments.get("y"), (int, float)) else None,
                delta_x=float(arguments.get("delta_x", 0)),
                delta_y=float(arguments.get("delta_y", 0)),
                width=arguments.get("width") if isinstance(arguments.get("width"), int) else None,
                height=arguments.get("height")
                if isinstance(arguments.get("height"), int)
                else None,
                checked=arguments.get("checked")
                if isinstance(arguments.get("checked"), bool)
                else None,
                timeout_ms=arguments.get("timeout_ms")
                if isinstance(arguments.get("timeout_ms"), int)
                else None,
                download_max_bytes=arguments.get("max_bytes")
                if isinstance(arguments.get("max_bytes"), int)
                else None,
                expected_page_revision=self._active_tab_revision(session, tab_id),
                idempotency_key=_browser_action_key(
                    scope=scope,
                    tool_name=definition.name,
                    page_revision=self._active_tab_revision(session, tab_id),
                    arguments=arguments,
                ),
            )
        )
        download_detail = ""
        if result.download is not None:
            download_detail = (
                f"\nDownloaded: {result.download.file_name} ({result.download.media_type}, "
                f"{result.download.size_bytes} bytes)\nSHA-256: {result.download.sha256}\n"
                f"Source: {result.download.source_url}\nStored: {result.download.local_path}"
            )
        return ToolResult.create(
            public_summary=result.public_summary,
            model_content=(
                f"{result.public_summary}\nURL: {result.tab.url}\nTitle: {result.tab.title}"
                f"{download_detail}"
            ),
            artifact_ids=(),
        )

    @staticmethod
    def _active_tab_revision(session: BrowserSessionModel, tab_id: UUID) -> int:
        tab = next((item for item in session.tabs if item.id == tab_id), None)
        if tab is None:
            raise ToolExecutionUnavailableError("Browser session active tab is unavailable")
        return tab.revision

    def close(self) -> None:
        close = getattr(self._delegate, "close", None)
        if callable(close):
            close()


def _browser_action_key(
    *,
    scope: ScopeContract,
    tool_name: str,
    page_revision: int,
    arguments: dict[str, object],
) -> str:
    encoded = json.dumps(
        arguments,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"assistant:{scope.task_id}:{tool_name}:{page_revision}:{digest}"


def _model_image_from_snapshot(
    scope: ScopeContract,
    snapshot: BrowserSnapshotModel,
) -> ModelImage:
    value = snapshot.screenshot_data_url
    prefix = "data:image/png;base64,"
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ToolExecutionUnavailableError("Browser did not return a PNG screenshot")
    encoded = value[len(prefix) :]
    if not encoded or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4 + 4:
        raise ToolExecutionUnavailableError("Browser screenshot exceeds the byte limit")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ToolExecutionUnavailableError("Browser screenshot encoding is invalid") from error
    width, height = parse_png_dimensions(content)
    if (width, height) != (snapshot.viewport_width, snapshot.viewport_height):
        raise ToolExecutionUnavailableError("Browser screenshot dimensions are inconsistent")
    buffer = bytearray(content)
    return ModelImage.create(
        task_id=scope.task_id,
        media_type="image/png",
        data=memoryview(buffer),
        content_hash=hashlib.sha256(content).hexdigest(),
        width=width,
        height=height,
        label="untrusted_screen_content",
        untrusted_data=True,
    )


def browser_service_handlers(service: BrowserService | None) -> Mapping[str, object]:
    selected = service or BrowserService(
        worker=None,
        state_path=Path("browser-unavailable.json"),
        profile_root=Path("browser-profile"),
    )
    return selected.handlers


def _validated_url(value: str) -> str:
    if value == "about:blank":
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Browser URL must use HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("Browser URL must not contain credentials")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except OSError as error:
        raise ValueError("Browser destination could not be resolved") from error
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_multicast
            or ip.is_unspecified
            or ip.is_link_local
            or (ip.is_private and not ip.is_loopback)
        ):
            raise ValueError("Browser destination is blocked")
    return value


__all__ = [
    "BrowserService",
    "BrowserToolExecutor",
    "BrowserWorker",
    "browser_service_handlers",
]
