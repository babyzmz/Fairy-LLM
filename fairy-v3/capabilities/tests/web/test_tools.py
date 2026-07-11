from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from fairy_core.assistant.tools import ToolResult
from fairy_core.commanding.registry import build_default_registry
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import OperationMode, ScopeContract, WorkspaceType
from fairy_core.research.models import (
    FetchedDocument,
    FetchRequest,
    ResearchCapabilityHealth,
    ResearchCapabilityStatus,
    SearchHit,
    SearchRequest,
)

from fairy_capabilities.web.tools import WebToolExecutor


class _Search:
    def health(self) -> ResearchCapabilityHealth:
        return ResearchCapabilityHealth.create(
            provider="fixture",
            status=ResearchCapabilityStatus.AVAILABLE,
            observed_at=datetime(2026, 7, 11, tzinfo=UTC),
            error_code=None,
        )

    def search(self, request: SearchRequest) -> tuple[SearchHit, ...]:
        assert request.query == "Fairy"
        return (
            SearchHit.create(
                provider="fixture",
                rank=1,
                title="Fairy",
                url="https://example.com/fairy",
                description="Project-first",
            ),
        )


class _Fetch:
    def fetch(self, request: FetchRequest) -> FetchedDocument:
        body = b"Fetched evidence"
        return FetchedDocument.create(
            requested_url=request.url,
            final_url=request.url,
            redirect_chain=(request.url,),
            media_type="text/plain",
            byte_length=len(body),
            content_hash=hashlib.sha256(body).hexdigest(),
            title="Evidence",
            text=body.decode(),
            fetched_at=datetime(2026, 7, 11, tzinfo=UTC),
        )


class _Delegate:
    def __init__(self) -> None:
        self.called = False

    def execute(self, definition, scope, arguments) -> ToolResult:
        del definition, scope, arguments
        self.called = True
        return ToolResult.create(
            public_summary="delegated",
            model_content="delegated",
            artifact_ids=(),
        )


def _scope(network_policy: str) -> ScopeContract:
    root = Path("C:/fairy/scratch")
    return ScopeContract.create(
        workspace_type=WorkspaceType.CHAT_SCRATCH,
        project_id=None,
        conversation_id=new_id(),
        task_id=new_id(),
        operation_mode=OperationMode.ANSWER,
        base_version_id=None,
        target_version_id=None,
        project_root=root,
        allowed_write_paths=(root,),
        forbidden_write_paths=(),
        execution_target="local",
        network_policy=network_policy,
        memory_read_scope=("current_conversation",),
        memory_write_scope=("current_conversation_draft",),
    )


def test_web_tool_executor_returns_bounded_source_labelled_results() -> None:
    registry = build_default_registry()
    delegate = _Delegate()
    executor = WebToolExecutor(
        search_port=_Search(),
        fetch_port=_Fetch(),
        delegate=delegate,
    )

    search = executor.execute(
        registry.get("web.search"),
        _scope("open_web_safe"),
        {"query": "Fairy", "kind": "web", "count": 1},
    )
    fetched = executor.execute(
        registry.get("web.fetch"),
        _scope("project_safe"),
        {"url": "https://example.com/fairy"},
    )
    delegated = executor.execute(registry.get("project.read"), None, {})

    assert "[SEARCH_RESULT rank=1 untrusted=true]" in search.model_content
    assert "https://example.com/fairy" in search.model_content
    assert "[FETCHED_DOCUMENT untrusted=true" in fetched.model_content
    assert "Fetched evidence" in fetched.model_content
    assert delegated.public_summary == "delegated"
    assert delegate.called is True


def test_web_tool_executor_rejects_scope_without_public_network_access() -> None:
    registry = build_default_registry()
    executor = WebToolExecutor(search_port=_Search(), fetch_port=_Fetch())

    for tool_name, arguments in (
        ("web.search", {"query": "Fairy"}),
        ("web.fetch", {"url": "https://example.com/fairy"}),
    ):
        try:
            executor.execute(
                registry.get(tool_name),
                _scope("off"),
                arguments,
            )
        except Exception as error:
            assert getattr(error, "error_code", None) == "NETWORK_ACCESS_BLOCKED"
        else:
            raise AssertionError("network-disabled Scope must reject web tools")
