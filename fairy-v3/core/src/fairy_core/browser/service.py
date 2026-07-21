from __future__ import annotations

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

from fairy_core.assistant.tools import ToolExecutionUnavailableError, ToolExecutor, ToolResult
from fairy_core.commanding.registry import ToolDefinition
from fairy_core.contracts.browser import (
    BrowserActionInput,
    BrowserActionResultModel,
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
            return BrowserWorkerHealthModel.model_validate(self._worker.call("browser.health", {}))
        except Exception as error:
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
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Browser session not found: {session_id}")
        return session

    def list(self, request: BrowserSessionListInput) -> BrowserSessionPageModel:
        terminal = {BrowserSessionStatus.STOPPED, BrowserSessionStatus.FAILED}
        with self._lock:
            items = tuple(
                session
                for session in self._sessions.values()
                if (
                    request.conversation_id is None
                    or session.conversation_id == request.conversation_id
                )
                and (request.task_id is None or session.task_id == request.task_id)
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
        request = BrowserSessionStartInput(
            project_id=session.project_id,
            conversation_id=session.conversation_id,
            task_id=session.task_id,
            execution_target=session.execution_target,
            profile_kind=session.profile_kind,
            initial_url=session.tabs[0].url if session.tabs else "about:blank",
            idempotency_key=f"resume:{session.id}:{session.revision}",
        )
        return self.start(request)

    def open_tab(self, request: BrowserTabOpenInput) -> BrowserSessionModel:
        result = self._required_worker().call(
            "browser.tabs.open",
            {"session_id": str(request.session_id), "url": _validated_url(request.url)},
        )
        return self._replace_from_worker(request.session_id, result)

    def select_tab(self, request: BrowserTabIdInput) -> BrowserSessionModel:
        result = self._required_worker().call(
            "browser.tabs.select",
            {"session_id": str(request.session_id), "tab_id": str(request.tab_id)},
        )
        return self._replace_from_worker(request.session_id, result)

    def close_tab(self, request: BrowserTabIdInput) -> BrowserSessionModel:
        result = self._required_worker().call(
            "browser.tabs.close",
            {"session_id": str(request.session_id), "tab_id": str(request.tab_id)},
        )
        return self._replace_from_worker(request.session_id, result)

    def execute(self, request: BrowserActionInput) -> BrowserActionResultModel:
        params = request.model_dump(mode="json")
        if request.kind.value == "navigate":
            params["value"] = _validated_url(request.value or "")
        result = self._required_worker().call("browser.actions.execute", params)
        session = self._replace_from_worker(request.session_id, result["session"])
        tab = BrowserTabModel.model_validate(result["tab"])
        return BrowserActionResultModel(
            session=session,
            tab=tab,
            public_summary=str(result.get("public_summary", "Browser action completed")),
            replayed=bool(result.get("replayed", False)),
        )

    def snapshot(self, request: BrowserSnapshotInput) -> BrowserSnapshotModel:
        result = self._required_worker().call(
            "browser.snapshots.get", request.model_dump(mode="json")
        )
        return BrowserSnapshotModel.model_validate(result)

    def session_for_scope(self, scope: ScopeContract) -> BrowserSessionModel:
        matches = self.list(
            BrowserSessionListInput(conversation_id=scope.conversation_id, task_id=scope.task_id)
        ).items
        if matches:
            return matches[0]
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
            session = previous.model_copy(
                update={
                    "status": BrowserSessionStatus(payload.get("status", "active")),
                    "active_tab_id": UUID(payload["active_tab_id"])
                    if payload.get("active_tab_id")
                    else None,
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
        return self._worker

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


class BrowserToolExecutor:
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
            snapshot = self._service.snapshot(
                BrowserSnapshotInput(session_id=session.id, tab_id=tab_id, include_screenshot=False)
            )
            return ToolResult.create(
                public_summary="Inspected the current web page",
                model_content=(
                    f"URL: {snapshot.url}\nTitle: {snapshot.title}\n{snapshot.aria_snapshot}"
                ),
                artifact_ids=(),
            )
        kind = definition.name.removeprefix("browser.")
        result = self._service.execute(
            BrowserActionInput(
                session_id=session.id,
                tab_id=tab_id,
                kind=kind,
                selector=arguments.get("selector")
                if isinstance(arguments.get("selector"), str)
                else None,
                value=arguments.get("value") if isinstance(arguments.get("value"), str) else None,
                delta_y=float(arguments.get("delta_y", 0)),
                idempotency_key=f"assistant:{scope.task_id}:{definition.name}:{uuid4()}",
            )
        )
        return ToolResult.create(
            public_summary=result.public_summary,
            model_content=(
                f"{result.public_summary}\nURL: {result.tab.url}\nTitle: {result.tab.title}"
            ),
            artifact_ids=(),
        )

    def close(self) -> None:
        close = getattr(self._delegate, "close", None)
        if callable(close):
            close()


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
