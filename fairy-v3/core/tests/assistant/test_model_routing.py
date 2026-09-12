from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from fairy_core.assistant.routing import (
    DEEPSEEK_MODEL_ID,
    GLM_MODEL_ID,
    IMAGE_MODEL_ID,
    KIMI_MODEL_ID,
    QWEN_FREE_MODEL_ID,
    RoutingTaskKind,
    auto_routing_decision,
    parse_router_output,
)
from fairy_core.model_catalog.models import (
    MODEL_ALLOWLIST,
    ModelAvailability,
    ModelCatalogEntry,
    ModelCatalogSnapshot,
    ModelEndpointKind,
    ModelPrice,
    ProviderAccount,
    ProviderCredentialStatus,
)
from fairy_core.model_catalog.ports import ModelCatalogFetchResult
from fairy_core.persona import load_default_persona_authority
from fairy_core.providers import (
    ModelDelta,
    ModelExecutionRole,
    ProviderCapability,
    ProviderRegistry,
)
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import RecordingToolExecutor, ScriptedProvider, wait_for_turn


class PricedCatalogSource:
    def __init__(self, *, unavailable: frozenset[str] = frozenset()) -> None:
        self._unavailable = unavailable

    def fetch(self) -> ModelCatalogFetchResult:
        return ModelCatalogFetchResult(
            entries=tuple(
                ModelCatalogEntry(
                    model_id=allowed.model_id,
                    display_name=allowed.display_name,
                    category=allowed.category,
                    endpoint_kind=allowed.endpoint_kind,
                    description=allowed.description,
                    paid=allowed.paid,
                    availability=(
                        ModelAvailability.UNAVAILABLE
                        if allowed.model_id in self._unavailable
                        else ModelAvailability.AVAILABLE
                    ),
                    unavailable_reason=(
                        "MODEL_NOT_LISTED" if allowed.model_id in self._unavailable else None
                    ),
                    input_modalities=("text",),
                    output_modalities=(
                        "text"
                        if allowed.endpoint_kind is ModelEndpointKind.CHAT
                        else allowed.endpoint_kind.value,
                    ),
                    supports_tools=allowed.endpoint_kind is ModelEndpointKind.CHAT,
                    supports_structured_output=(allowed.endpoint_kind is ModelEndpointKind.CHAT),
                    prices=(
                        (
                            ModelPrice(
                                billable="prompt",
                                unit="token",
                                cost_usd="0.0000001",
                            ),
                            ModelPrice(
                                billable="completion",
                                unit="token",
                                cost_usd="0.0000002",
                            ),
                        )
                        if allowed.paid and allowed.endpoint_kind is ModelEndpointKind.CHAT
                        else (
                            ModelPrice(
                                billable="generation",
                                unit="request",
                                cost_usd="0.04",
                                variant="1k",
                            ),
                        )
                        if allowed.endpoint_kind is ModelEndpointKind.IMAGES
                        else ()
                    ),
                )
                for allowed in MODEL_ALLOWLIST
            ),
            credential_status=ProviderCredentialStatus.CONFIGURED,
            fetched_at=datetime.now(UTC),
        )

    def close(self) -> None:
        pass


def _route_delta(
    *,
    profile_id: str,
    task_kind: str = "general",
    complexity: str = "low",
    needs_review: bool = False,
    requires_workspace_changes: bool = False,
) -> tuple[ModelDelta, ...]:
    return (
        ModelDelta.text(
            profile_id=profile_id,
            sequence=1,
            text=json.dumps(
                {
                    "task_kind": task_kind,
                    "complexity": complexity,
                    "needs_review": needs_review,
                    "requires_workspace_changes": requires_workspace_changes,
                    "estimated_output_tokens": 512,
                    "public_summary": "DeepSeek will handle this general request.",
                },
                separators=(",", ":"),
            ),
        ),
        ModelDelta.done(profile_id=profile_id, sequence=2, finish_reason="stop"),
    )


def _provider(
    *,
    profile_id: str,
    model_id: str,
    rounds: list[tuple[ModelDelta, ...]],
) -> ScriptedProvider:
    return ScriptedProvider(
        rounds,
        profile_id=profile_id,
        model_id=model_id,
        capabilities=frozenset(
            {
                ProviderCapability.TEXT,
                ProviderCapability.TOOLS,
                ProviderCapability.STRUCTURED_OUTPUT,
            }
        ),
    )


def _catalog_snapshot(
    *,
    unavailable: frozenset[str] = frozenset(),
) -> ModelCatalogSnapshot:
    fetched = PricedCatalogSource(unavailable=unavailable).fetch()
    return ModelCatalogSnapshot(
        account=ProviderAccount(
            account_id="openrouter-default",
            provider_kind="openrouter",
            display_name="OpenRouter",
            credential_status=fetched.credential_status,
        ),
        entries=fetched.entries,
        fetched_at=fetched.fetched_at,
        expires_at=fetched.fetched_at + timedelta(hours=6),
        revision=1,
    )


def test_browser_qa_intent_cannot_be_upgraded_to_paid_image_generation() -> None:
    request = (
        "\u4f7f\u7528 Browser \u68c0\u67e5\u521a\u751f\u6210\u7684\u7f51\u9875\uff0c"
        "\u70b9\u51fb\u4e3b\u8981\u6309\u94ae\uff0c\u6eda\u52a8\u5230\u9875\u9762\u5e95\u90e8\uff0c"
        "\u5206\u522b\u622a\u53d6\u684c\u9762\u548c\u79fb\u52a8\u7aef\u622a\u56fe\uff0c"
        "\u5e76\u603b\u7ed3\u53d1\u73b0\u7684\u95ee\u9898\u3002"
    )
    routed = parse_router_output(
        json.dumps(
            {
                "task_kind": "image",
                "complexity": "high",
                "needs_review": True,
                "requires_workspace_changes": True,
                "estimated_output_tokens": 1024,
                "public_summary": "Generate screenshots as images.",
            }
        )
    )

    decision = auto_routing_decision(
        routed=routed,
        catalog=_catalog_snapshot(),
        user_request=request,
        attachment_count=0,
        allow_free_fallback=False,
    )

    assert decision.task_kind is RoutingTaskKind.BROWSER
    assert decision.primary_model_id == KIMI_MODEL_ID
    assert decision.reviewer_model_id is None
    assert decision.media_model_id is None
    assert decision.requires_workspace_changes is False
    assert decision.public_summary == "Inspect the current Preview with the scoped Browser."


def test_explicit_image_generation_remains_a_media_route() -> None:
    routed = parse_router_output(
        json.dumps(
            {
                "task_kind": "image",
                "complexity": "low",
                "needs_review": False,
                "requires_workspace_changes": False,
                "estimated_output_tokens": 512,
                "public_summary": "Generate the requested hero image.",
            }
        )
    )

    decision = auto_routing_decision(
        routed=routed,
        catalog=_catalog_snapshot(),
        user_request=(
            "\u4e3a\u5f53\u524d\u7f51\u9875\u751f\u6210\u4e00\u5f20\u65b0\u7684 Hero \u56fe\u7247"
        ),
        attachment_count=0,
        allow_free_fallback=False,
    )

    assert decision.task_kind is RoutingTaskKind.IMAGE
    assert decision.media_model_id == "google/gemini-3.1-flash-lite-image"


def test_browser_route_does_not_downgrade_to_a_non_visual_model() -> None:
    routed = parse_router_output(
        json.dumps(
            {
                "task_kind": "browser",
                "complexity": "medium",
                "needs_review": False,
                "requires_workspace_changes": False,
                "estimated_output_tokens": 512,
                "public_summary": "Inspect the Preview.",
            }
        )
    )

    decision = auto_routing_decision(
        routed=routed,
        catalog=_catalog_snapshot(unavailable=frozenset({KIMI_MODEL_ID})),
        user_request="Use Browser to inspect and capture the current Preview.",
        attachment_count=0,
        allow_free_fallback=True,
    )

    assert decision.primary_model_id == KIMI_MODEL_ID


def test_browser_qa_overrides_a_code_misroute_without_workspace_mutation() -> None:
    routed = parse_router_output(
        json.dumps(
            {
                "task_kind": "code",
                "complexity": "medium",
                "needs_review": True,
                "requires_workspace_changes": True,
                "estimated_output_tokens": 1024,
                "public_summary": "Inspect the generated source.",
            }
        )
    )

    decision = auto_routing_decision(
        routed=routed,
        catalog=_catalog_snapshot(),
        user_request="Use Browser to inspect, click, scroll, and capture the current Preview.",
        attachment_count=0,
        allow_free_fallback=False,
    )

    assert decision.task_kind is RoutingTaskKind.BROWSER
    assert decision.requires_workspace_changes is False
    assert decision.reviewer_model_id is None


def test_browser_repair_request_remains_a_code_workspace_task() -> None:
    routed = parse_router_output(
        json.dumps(
            {
                "task_kind": "browser",
                "complexity": "medium",
                "needs_review": False,
                "requires_workspace_changes": True,
                "estimated_output_tokens": 1024,
                "public_summary": "Inspect and repair the Preview.",
            }
        )
    )

    decision = auto_routing_decision(
        routed=routed,
        catalog=_catalog_snapshot(),
        user_request="Use Browser to inspect the Preview, then fix the broken navigation.",
        attachment_count=0,
        allow_free_fallback=False,
    )

    assert decision.task_kind is RoutingTaskKind.CODE
    assert decision.requires_workspace_changes is True


def _task(service, request: str) -> dict[str, object]:
    conversation = service.invoke(
        "conversations.create",
        {"project_id": None, "workspace_type": "chat_scratch"},
    )
    return service.invoke(
        "tasks.create",
        {
            "conversation_id": conversation["id"],
            "user_request": request,
            "operation_mode": "answer",
            "execution_target": "local",
            "idempotency_key": f"task:{conversation['id']}",
        },
    )["task"]


def _auto_turn(service, task: dict[str, object], key: str) -> dict[str, object]:
    selection = service.invoke("models.selection.get", {})
    return service.invoke(
        "assistant.turns.create",
        {
            "task_id": task["id"],
            "model_selection": {
                "mode": selection["mode"],
                "model_id": selection["model_id"],
                "revision": selection["revision"],
            },
            "idempotency_key": key,
        },
    )


def test_paid_model_budget_approval_emits_started_message_without_worker_lease(
    tmp_path: Path,
) -> None:
    deepseek = _provider(
        profile_id="openrouter-deepseek-v4-pro",
        model_id=DEEPSEEK_MODEL_ID,
        rounds=[
            (
                ModelDelta.text(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=1,
                    text=json.dumps(
                        {
                            "evidence_requirements": [],
                            "requires_workspace_changes": False,
                            "public_summary": "Explain the request.",
                        }
                    ),
                ),
                ModelDelta.done(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=2,
                    finish_reason="stop",
                ),
            )
        ],
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((deepseek,)),
    )
    try:
        service.invoke(
            "models.selection.update",
            {
                "mode": "manual",
                "model_id": DEEPSEEK_MODEL_ID,
                "allow_free_fallback": False,
                "zero_data_retention": False,
                "expected_revision": 0,
                "idempotency_key": "paid-approval-selection",
            },
        )
        task = _task(service, "Explain this paid-model request")
        turn = _auto_turn(service, task, "turn:paid-approval")

        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        waiting = wait_for_turn(
            service,
            turn["id"],
            status="waiting_for_tool",
        )
        events = service.invoke("events.list", {"cursor": 0, "limit": 100})["items"]

        assert waiting["error_code"] is None
        assert any(
            event["event_type"] == "message.created"
            and event["payload"].get("turn_id") == turn["id"]
            for event in events
        )
        assert any(
            event["event_type"] == "assistant.budget.approval_requested"
            and event["payload"].get("turn_id") == turn["id"]
            for event in events
        )
        assert len(deepseek.requests) == 1
        assert deepseek.requests[0].model_role is ModelExecutionRole.COORDINATOR
    finally:
        service.close()


def test_manual_model_classifies_evidence_and_rejects_uncited_plain_text(
    tmp_path: Path,
) -> None:
    qwen = _provider(
        profile_id="openrouter-qwen-free",
        model_id=QWEN_FREE_MODEL_ID,
        rounds=[
            (
                ModelDelta.text(
                    profile_id="openrouter-qwen-free",
                    sequence=1,
                    text=json.dumps(
                        {
                            "evidence_requirements": ["workspace_structure"],
                            "requires_workspace_changes": False,
                            "public_summary": "Inspect the current Workspace layout.",
                        }
                    ),
                ),
                ModelDelta.done(
                    profile_id="openrouter-qwen-free",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
            (
                ModelDelta.text(
                    profile_id="openrouter-qwen-free",
                    sequence=1,
                    text="I remember the files without checking.",
                ),
                ModelDelta.done(
                    profile_id="openrouter-qwen-free",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
            (
                ModelDelta.text(
                    profile_id="openrouter-qwen-free",
                    sequence=1,
                    text="I still will not inspect them.",
                ),
                ModelDelta.done(
                    profile_id="openrouter-qwen-free",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ],
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((qwen,)),
    )
    try:
        service.invoke(
            "models.selection.update",
            {
                "mode": "manual",
                "model_id": QWEN_FREE_MODEL_ID,
                "allow_free_fallback": False,
                "zero_data_retention": False,
                "expected_revision": 0,
                "idempotency_key": "manual-evidence-selection",
            },
        )
        task = _task(service, "List the files that are currently in this Workspace")
        turn = _auto_turn(service, task, "turn:manual-evidence")

        failed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert failed["status"] == "failed"
        assert failed["error_code"] == "EVIDENCE_CITATION_REQUIRED"
        assert failed["routing_decision"]["evidence_requirements"] == ["workspace_structure"]
        assert failed["routing_decision"]["evidence_classified"] is True
        direct_answer_tool = qwen.requests[1].tools[0]
        assert direct_answer_tool.name == "direct_answer"
        assert "evidence_receipt_ids" in direct_answer_tool.input_schema["required"]
    finally:
        service.close()


def test_manual_classifier_resolves_a_trusted_mcp_target_then_requires_approval(tmp_path: Path):
    from tests.mcp.support import FakeMcpConnector, issue_tools
    from tests.mcp.test_tool_execution import _trusted_service

    profile = "openrouter-qwen-free"
    target = "mcp.issue-tracker.create_issue"
    provider = _provider(profile_id=profile, model_id=QWEN_FREE_MODEL_ID, rounds=[
        (
            ModelDelta.text(profile_id=profile, sequence=1, text=json.dumps({
                "evidence_requirements": [], "requires_workspace_changes": False,
                "public_summary": "Create the requested issue in the connected tracker",
                "interpretation": {
                    "normalized_goal": "Create one Beta checklist issue", "action": "create",
                    "objectives": [{"goal": "Create one Beta checklist issue", "action": "create"}],
                    "targets": [target], "confidence": "high", "disposition": "ready",
                    "public_summary": "Create one Beta checklist issue",
                },
            })),
            ModelDelta.done(profile_id=profile, sequence=2, finish_reason="stop"),
        ),
        (
            ModelDelta.tool_call(
                profile_id=profile, sequence=1, tool_call_id="classified-mcp-create",
                tool_name=target, arguments_fragment='{"title":"Beta checklist"}',
            ),
            ModelDelta.done(profile_id=profile, sequence=2, finish_reason="tool_calls"),
        ),
        (
            ModelDelta.text(profile_id=profile, sequence=1, text="Created one issue."),
            ModelDelta.done(profile_id=profile, sequence=2, finish_reason="stop"),
        ),
    ])
    connector = FakeMcpConnector(issue_tools())
    service, task = _trusted_service(
        tmp_path, provider, connector,
        request="Create one issue titled Beta checklist in the connected issue tracker",
    )
    try:
        service.invoke("models.selection.update", {
            "mode": "manual", "model_id": QWEN_FREE_MODEL_ID,
            "allow_free_fallback": False, "zero_data_retention": False,
            "expected_revision": 0, "idempotency_key": "mcp-classifier-model",
        })
        turn = _auto_turn(service, task, "classified-mcp")
        waiting = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        assert waiting["status"] == "waiting_for_tool"
        assert connector.calls == []
        envelope = json.loads(provider.requests[0].messages[-1].content)
        assert envelope["available_mcp_action_targets"] == [target]
        approval = service.invoke("approvals.list", {"task_id": task["id"]})["items"][0]
        service.invoke("approvals.decide", {"approval_id": approval["id"], "approved": True})
        assert wait_for_turn(service, turn["id"])["status"] == "completed"
        assert len(connector.calls) == 1
        assert connector.calls[0][2] == {"title": "Beta checklist"}
    finally:
        service.close()


def test_route_only_classifier_cannot_grant_execution_authority(tmp_path: Path) -> None:
    from fairy_core.assistant.execution_intent_policy import readonly_intent_issue
    from fairy_core.commanding.registry import build_default_registry
    from fairy_core.providers import CancellationToken

    profile = "openrouter-qwen-free"
    rounds = []
    for changes in (True, False):
        rounds.append(
            (
                ModelDelta.text(
                    profile_id=profile,
                    sequence=1,
                    text=json.dumps({
                        "evidence_requirements": [],
                        "requires_workspace_changes": changes,
                        "public_summary": "Routing metadata without an intent contract.",
                    }),
                ),
                ModelDelta.done(profile_id=profile, sequence=2, finish_reason="stop"),
            )
        )
    qwen = _provider(profile_id=profile, model_id=QWEN_FREE_MODEL_ID, rounds=rounds)
    service = build_local_service(tmp_path, provider_registry=ProviderRegistry((qwen,)))
    try:
        service.invoke("models.selection.update", {
            "mode": "manual", "model_id": QWEN_FREE_MODEL_ID,
            "allow_free_fallback": False, "zero_data_retention": False,
            "expected_revision": 0, "idempotency_key": "incomplete-classifier-selection",
        })
        for index, request in enumerate(("Handle the project", "Explain the project")):
            task = _task(service, request)
            turn = _auto_turn(service, task, f"route-only:{index}")
            application = service._assistant_application
            application._ensure_routing(
                application._turns.get(UUID(turn["id"])), CancellationToken(),
            )
            with service._unit_of_work_factory() as unit:
                intent = unit.assistant.get_execution_intent(UUID(turn["id"]))
                assert intent is not None
                assert intent.conversation_id == UUID(task["conversation_id"])
                run_tool = next(
                    tool for tool in build_default_registry().definitions()
                    if tool.name == "run.sandboxed"
                )
                assert readonly_intent_issue(intent, run_tool) == "EXECUTION_INTENT_READ_ONLY"
            context = application._context.build(
                application._turns.get(UUID(turn["id"])),
                provider_capabilities=frozenset({
                    ProviderCapability.TEXT, ProviderCapability.TOOLS,
                }),
            )
            offered = {tool.name for tool in context.tools}
            assert "run.sandboxed" not in offered
            assert "deps.install" not in offered
            assert "project.read" in offered
    finally:
        service.close()


def test_manual_media_is_interpreted_before_generation(tmp_path: Path) -> None:
    deepseek = _provider(
        profile_id="openrouter-deepseek-v4-pro",
        model_id=DEEPSEEK_MODEL_ID,
        rounds=[
            (
                ModelDelta.text(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=1,
                    text=json.dumps(
                        {
                            "evidence_requirements": [],
                            "requires_workspace_changes": False,
                            "public_summary": "Clarify the requested image.",
                            "interpretation": {
                                "normalized_goal": "Generate an unspecified image",
                                "action": "generate",
                                "objectives": [
                                    {
                                        "goal": "Generate an unspecified image",
                                        "action": "generate",
                                    }
                                ],
                                "targets": [],
                                "constraints": [],
                                "deliverable": None,
                                "assumptions": [],
                                "missing_information": [],
                                "confidence": "low",
                                "disposition": "ready",
                                "public_summary": "Clarify the requested image.",
                                "clarification_question": None,
                            },
                        }
                    ),
                ),
                ModelDelta.done(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=2,
                    finish_reason="stop",
                ),
            )
        ],
    )
    image = _provider(
        profile_id="openrouter-gemini-image",
        model_id=IMAGE_MODEL_ID,
        rounds=[],
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((deepseek, image)),
        model_catalog_source=PricedCatalogSource(),
    )
    try:
        service.invoke("models.catalog.refresh", {})
        service.invoke(
            "models.selection.update",
            {
                "mode": "manual",
                "model_id": IMAGE_MODEL_ID,
                "allow_free_fallback": False,
                "zero_data_retention": False,
                "expected_revision": 0,
                "idempotency_key": "manual-image-selection",
            },
        )
        task = _task(service, "Make it")
        turn = _auto_turn(service, task, "turn:manual-image-clarification")

        waiting = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert waiting["status"] == "waiting_for_input"
        assert waiting["interpretation_summary"]["action"] == "generate"
        assert (
            waiting["interpretation_summary"]["disposition"]
            == "clarification_required"
        )
        assert len(deepseek.requests) == 1
        assert image.requests == []
    finally:
        service.close()


def test_manual_evidence_classifier_fails_closed_on_invalid_output(tmp_path: Path) -> None:
    qwen = _provider(
        profile_id="openrouter-qwen-free",
        model_id=QWEN_FREE_MODEL_ID,
        rounds=[
            (
                ModelDelta.text(
                    profile_id="openrouter-qwen-free",
                    sequence=1,
                    text="not structured evidence classification",
                ),
                ModelDelta.done(
                    profile_id="openrouter-qwen-free",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ],
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((qwen,)),
    )
    try:
        service.invoke(
            "models.selection.update",
            {
                "mode": "manual",
                "model_id": QWEN_FREE_MODEL_ID,
                "allow_free_fallback": False,
                "zero_data_retention": False,
                "expected_revision": 0,
                "idempotency_key": "invalid-evidence-selection",
            },
        )
        task = _task(service, "What is currently in this Workspace?")
        turn = _auto_turn(service, task, "turn:invalid-evidence")

        failed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert failed["status"] == "failed"
        assert failed["error_code"] == "EVIDENCE_CLASSIFICATION_FAILED"
        assert len(qwen.requests) == 1
    finally:
        service.close()


def test_auto_router_is_durable_and_never_projects_router_text(tmp_path: Path) -> None:
    deepseek = _provider(
        profile_id="openrouter-deepseek-v4-pro",
        model_id=DEEPSEEK_MODEL_ID,
        rounds=[
            _route_delta(profile_id="openrouter-deepseek-v4-pro"),
            (
                ModelDelta.text(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=1,
                    text="One visible answer.",
                ),
                ModelDelta.done(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ],
    )
    glm = _provider(
        profile_id="openrouter-glm-5-2",
        model_id=GLM_MODEL_ID,
        rounds=[],
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((deepseek, glm)),
        model_catalog_source=PricedCatalogSource(),
    )
    try:
        service.invoke("models.catalog.refresh", {})
        task = _task(service, "Explain the current architecture briefly")
        turn = _auto_turn(service, task, "turn:auto")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]
        events = service.invoke("events.subscribe", {"cursor": 0})["items"]
        trace = service.invoke("assistant.turns.trace.list", {"turn_id": turn["id"]})
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            workflow = unit_of_work.workflows.get(UUID(completed["workflow_run_id"]))

        assert completed["status"] == "completed", (
            completed["error_code"],
            [
                (event["event_type"], event["payload"])
                for event in events
                if event["payload"].get("turn_id") == turn["id"]
                or event["event_type"].startswith("assistant.")
            ],
        )
        assert completed["model_selection"]["mode"] == "auto"
        assert completed["routing_decision"]["primary_model_id"] == DEEPSEEK_MODEL_ID
        assert completed["routing_decision"]["approval_required"] is False
        assert [(item["role"], item["content"]) for item in messages] == [
            ("user", "Explain the current architecture briefly"),
            ("assistant", "One visible answer."),
        ]
        deltas = [
            event["payload"]["text"]
            for event in events
            if event["event_type"] == "assistant.message.delta"
        ]
        assert deltas == ["One visible answer."]
        assert sum(event["event_type"] == "assistant.route.selected" for event in events) == 1
        assert [request.model_role for request in deepseek.requests] == [
            ModelExecutionRole.COORDINATOR,
            ModelExecutionRole.PRIMARY,
        ]
        assert deepseek.requests[0].response_schema_name == "fairy_routing_decision"
        assert deepseek.requests[1].response_schema is None
        assert [step["kind"] for step in trace["steps"]] == [
            "model",
            "route",
            "model",
            "response",
        ]
        coordinator, route, primary, response = trace["steps"]
        assert coordinator["model_role"] == "coordinator"
        assert route["public_summary"] == "General task routed to DeepSeek V4 Pro"
        assert route["caused_by_step_id"] == coordinator["id"]
        assert primary["caused_by_step_id"] == route["id"]
        assert response["caused_by_step_id"] == primary["id"]
        assert all(step["status"] == "succeeded" for step in trace["steps"])
        assert workflow is not None
        assert tuple(node.kind for node in workflow.nodes) == (
            "assistant.request.interpret", "assistant.step.route", "assistant.step.model",
            "assistant.step.verify", "assistant.step.finalize",
        )
        assert all(node.status.value == "succeeded" for node in workflow.nodes)
        assert all(node.result is not None for node in workflow.nodes)
        assert workflow.nodes[-1].result == {
            "turn_id": turn["id"], "status": "completed",
        }
    finally:
        service.close()


def test_auto_router_classifies_long_requests_with_bounded_attempts(tmp_path: Path) -> None:
    deepseek = _provider(
        profile_id="openrouter-deepseek-v4-pro",
        model_id=DEEPSEEK_MODEL_ID,
        rounds=[
            _route_delta(profile_id="openrouter-deepseek-v4-pro"),
            _route_delta(
                profile_id="openrouter-deepseek-v4-pro",
                complexity="medium",
            ),
            (
                ModelDelta.text(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=1,
                    text="Long request handled.",
                ),
                ModelDelta.done(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ],
    )
    glm = _provider(
        profile_id="openrouter-glm-5-2",
        model_id=GLM_MODEL_ID,
        rounds=[],
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((deepseek, glm)),
        model_catalog_source=PricedCatalogSource(),
    )
    request_text = "Explain this request.\n" + " ".join("context" for _ in range(9_000))
    try:
        service.invoke("models.catalog.refresh", {})
        task = _task(service, request_text)
        turn = _auto_turn(service, task, "turn:auto-long-router")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert completed["status"] == "completed", (
            completed["error_code"],
            [(item.model_role, item.response_schema_name) for item in deepseek.requests],
        )
        assert completed["routing_decision"]["complexity"] == "medium"
        coordinator_requests = [
            item
            for item in deepseek.requests
            if item.model_role is ModelExecutionRole.COORDINATOR
        ]
        assert len(coordinator_requests) == 2
        payloads = [json.loads(item.messages[-1].content) for item in coordinator_requests]
        assert [payload["attempt_index"] for payload in payloads] == [0, 1]
        assert {payload["attempt_count"] for payload in payloads} == {2}
        assert (
            "".join(
                segment["text"]
                for payload in payloads
                for segment in payload["segments"]
            )
            == request_text
        )
    finally:
        service.close()


def test_auto_router_retries_invalid_buffered_output_before_execution(tmp_path: Path) -> None:
    deepseek = _provider(
        profile_id="openrouter-deepseek-v4-pro",
        model_id=DEEPSEEK_MODEL_ID,
        rounds=[
            (
                ModelDelta.text(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=1,
                    text="not valid routing json",
                ),
                ModelDelta.done(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
            _route_delta(profile_id="openrouter-deepseek-v4-pro"),
            (
                ModelDelta.text(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=1,
                    text="Recovered after routing validation.",
                ),
                ModelDelta.done(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ],
    )
    glm = _provider(
        profile_id="openrouter-glm-5-2",
        model_id=GLM_MODEL_ID,
        rounds=[],
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((deepseek, glm)),
        model_catalog_source=PricedCatalogSource(),
    )
    try:
        service.invoke("models.catalog.refresh", {})
        task = _task(service, "Explain the current architecture briefly")
        turn = _auto_turn(service, task, "turn:auto-router-retry")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]

        assert completed["status"] == "completed"
        assert completed["routing_decision"]["primary_model_id"] == DEEPSEEK_MODEL_ID
        assert messages[-1]["content"] == "Recovered after routing validation."
        assert [request.model_role for request in deepseek.requests] == [
            ModelExecutionRole.COORDINATOR,
            ModelExecutionRole.COORDINATOR,
            ModelExecutionRole.PRIMARY,
        ]
    finally:
        service.close()


def test_auto_workspace_delivery_cannot_complete_without_an_execution_plan(
    tmp_path: Path,
) -> None:
    deepseek = _provider(
        profile_id="openrouter-deepseek-v4-pro",
        model_id=DEEPSEEK_MODEL_ID,
        rounds=[
            _route_delta(
                profile_id="openrouter-deepseek-v4-pro",
                task_kind="code",
                complexity="medium",
                requires_workspace_changes=True,
            ),
        ],
    )
    kimi = _provider(
        profile_id="openrouter-kimi-k2-7-code",
        model_id=KIMI_MODEL_ID,
        rounds=[
            (
                ModelDelta.text(
                    profile_id="openrouter-kimi-k2-7-code",
                    sequence=1,
                    text="The page is ready.",
                ),
                ModelDelta.done(
                    profile_id="openrouter-kimi-k2-7-code",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
            (
                ModelDelta.text(
                    profile_id="openrouter-kimi-k2-7-code",
                    sequence=1,
                    text="Finished without writing files.",
                ),
                ModelDelta.done(
                    profile_id="openrouter-kimi-k2-7-code",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ],
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((deepseek, kimi)),
        model_catalog_source=PricedCatalogSource(),
    )
    try:
        service.invoke("models.catalog.refresh", {})
        task = _task(service, "Create a three-file GSAP page in the Workspace")
        turn = _auto_turn(service, task, "turn:workspace-delivery-gate")

        failed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]

        assert failed["status"] == "failed"
        assert failed["error_code"] == "WORKER_INTERRUPTED"
        assert failed["routing_decision"]["requires_workspace_changes"] is True
        assert "DELIVERY CONTRACT" in kimi.requests[0].messages[0].content
        assert [item for item in messages if item["role"] == "assistant"] == []
    finally:
        service.close()


def test_unknown_cost_route_waits_for_and_resumes_budget_approval(tmp_path: Path) -> None:
    deepseek = _provider(
        profile_id="openrouter-deepseek-v4-pro",
        model_id=DEEPSEEK_MODEL_ID,
        rounds=[
            _route_delta(profile_id="openrouter-deepseek-v4-pro"),
            (
                ModelDelta.text(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=1,
                    text="Approved answer.",
                ),
                ModelDelta.done(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ],
    )
    glm = _provider(
        profile_id="openrouter-glm-5-2",
        model_id=GLM_MODEL_ID,
        rounds=[],
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((deepseek, glm)),
    )
    try:
        task = _task(service, "Use the paid model with an unknown estimate")
        turn = _auto_turn(service, task, "turn:budget")

        waiting = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        approval = service.invoke("approvals.list", {"task_id": task["id"]})["items"][0]
        assert waiting["status"] == "waiting_for_tool"
        assert waiting["budget_approval_run_id"] == approval["command_run_id"]
        assert len(deepseek.requests) == 1

        service.invoke(
            "approvals.decide",
            {"approval_id": approval["id"], "approved": True},
        )
        completed = wait_for_turn(service, turn["id"])
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]

        assert completed["status"] == "completed"
        assert [item["role"] for item in messages] == ["user", "assistant"]
        assert messages[-1]["content"] == "Approved answer."
        assert len(deepseek.requests) == 2
    finally:
        service.close()


def test_multi_model_review_streams_only_the_single_final_answer(tmp_path: Path) -> None:
    deepseek = _provider(
        profile_id="openrouter-deepseek-v4-pro",
        model_id=DEEPSEEK_MODEL_ID,
        rounds=[
            _route_delta(
                profile_id="openrouter-deepseek-v4-pro",
                task_kind="reasoning",
                complexity="high",
                needs_review=True,
            ),
            (
                ModelDelta.text(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=1,
                    text="Reviewed final answer.",
                ),
                ModelDelta.done(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ],
    )
    glm = _provider(
        profile_id="openrouter-glm-5-2",
        model_id=GLM_MODEL_ID,
        rounds=[
            (
                ModelDelta.text(
                    profile_id="openrouter-glm-5-2",
                    sequence=1,
                    text="Unreviewed private draft.",
                ),
                ModelDelta.done(
                    profile_id="openrouter-glm-5-2",
                    sequence=2,
                    finish_reason="stop",
                ),
            )
        ],
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((deepseek, glm)),
        model_catalog_source=PricedCatalogSource(),
    )
    try:
        service.invoke("models.catalog.refresh", {})
        task = _task(service, "Resolve a difficult design tradeoff")
        turn = _auto_turn(service, task, "turn:review")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]
        events = service.invoke("events.subscribe", {"cursor": 0})["items"]
        trace = service.invoke("assistant.turns.trace.list", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        assert completed["routing_decision"]["primary_model_id"] == GLM_MODEL_ID
        assert completed["routing_decision"]["reviewer_model_id"] == DEEPSEEK_MODEL_ID
        assert completed["workflow_summary"]["budget_tier"] == "deep"
        assert completed["workflow_summary"]["max_model_rounds"] == 24
        assert completed["workflow_summary"]["max_tool_invocations"] == 96
        assert [(item["role"], item["content"]) for item in messages] == [
            ("user", "Resolve a difficult design tradeoff"),
            ("assistant", "Reviewed final answer."),
        ]
        assert [
            event["payload"]["text"]
            for event in events
            if event["event_type"] == "assistant.message.delta"
        ] == ["Reviewed final answer."]
        assert [request.model_role for request in deepseek.requests] == [
            ModelExecutionRole.COORDINATOR,
            ModelExecutionRole.REVIEWER,
        ]
        assert [request.model_role for request in glm.requests] == [ModelExecutionRole.PRIMARY]
        reviewer_request = deepseek.requests[-1]
        authority = load_default_persona_authority()
        assert authority.system_prompt in reviewer_request.messages[0].content
        assert "Never identify yourself as the provider or underlying model" in (
            reviewer_request.messages[0].content
        )
        assert reviewer_request.messages[-2].content == "Unreviewed private draft."
        assert [step["kind"] for step in trace["steps"]] == [
            "model",
            "route",
            "model",
            "model",
            "verification",
            "response",
        ]
        coordinator, route, primary, reviewer, verification, response = trace["steps"]
        assert route["caused_by_step_id"] == coordinator["id"]
        assert reviewer["caused_by_step_id"] == primary["id"]
        assert verification["parent_step_id"] == reviewer["id"]
        assert response["caused_by_step_id"] == verification["id"]
    finally:
        service.close()


def test_auto_route_selects_an_available_compatible_model(tmp_path: Path) -> None:
    kimi_model_id = "moonshotai/kimi-k2.7-code"
    deepseek = _provider(
        profile_id="openrouter-deepseek-v4-pro",
        model_id=DEEPSEEK_MODEL_ID,
        rounds=[
            _route_delta(
                profile_id="openrouter-deepseek-v4-pro",
                task_kind="code",
            ),
            (
                ModelDelta.text(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=1,
                    text="Fallback implementation.",
                ),
                ModelDelta.done(
                    profile_id="openrouter-deepseek-v4-pro",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ],
    )
    glm = _provider(
        profile_id="openrouter-glm-5-2",
        model_id=GLM_MODEL_ID,
        rounds=[],
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((deepseek, glm)),
        model_catalog_source=PricedCatalogSource(unavailable=frozenset({kimi_model_id})),
    )
    try:
        service.invoke("models.catalog.refresh", {})
        task = _task(service, "Implement a parser")
        turn = _auto_turn(service, task, "turn:available-fallback")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        assert completed["routing_decision"]["primary_model_id"] == DEEPSEEK_MODEL_ID
        assert "available model" in completed["routing_decision"]["public_summary"]
    finally:
        service.close()


def test_auto_code_route_discards_tool_preamble_and_completes_valid_calls(
    tmp_path: Path,
) -> None:
    deepseek = _provider(
        profile_id="openrouter-deepseek-v4-pro",
        model_id=DEEPSEEK_MODEL_ID,
        rounds=[
            _route_delta(
                profile_id="openrouter-deepseek-v4-pro",
                task_kind="code",
                complexity="medium",
            ),
        ],
    )
    kimi = _provider(
        profile_id="openrouter-kimi-k2-7-code",
        model_id=KIMI_MODEL_ID,
        rounds=[
            (
                ModelDelta.text(
                    profile_id="openrouter-kimi-k2-7-code",
                    sequence=1,
                    text="I will create the execution plan first.",
                ),
                ModelDelta.tool_call(
                    profile_id="openrouter-kimi-k2-7-code",
                    sequence=2,
                    tool_call_id="call-guidance",
                    tool_name="web.search",
                    arguments_fragment=(
                        '{"query":"GSAP ScrollTrigger",'
                        '"public_intent":"Check implementation guidance"}'
                    ),
                ),
                ModelDelta.done(
                    profile_id="openrouter-kimi-k2-7-code",
                    sequence=3,
                    finish_reason="tool_calls",
                ),
            ),
            (
                ModelDelta.text(
                    profile_id="openrouter-kimi-k2-7-code",
                    sequence=1,
                    text="The animated page is ready.",
                ),
                ModelDelta.done(
                    profile_id="openrouter-kimi-k2-7-code",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ],
    )
    executor = RecordingToolExecutor(summary="Validated GSAP guidance")
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((deepseek, kimi)),
        model_catalog_source=PricedCatalogSource(),
        tool_executor=executor,
    )
    try:
        service.invoke("models.catalog.refresh", {})
        task = _task(service, "Create a GSAP ScrollTrigger page")
        turn = _auto_turn(service, task, "turn:code-tool-preamble")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]
        events = service.invoke("events.subscribe", {"cursor": 0})["items"]
        trace = service.invoke("assistant.turns.trace.list", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        assert completed["routing_decision"]["primary_model_id"] == KIMI_MODEL_ID
        assert completed["routing_decision"]["estimated_output_tokens"] == 16_384
        assert len(executor.calls) == 1
        assert [item["content"] for item in messages if item["role"] == "assistant"] == [
            "The animated page is ready."
        ]
        resets = [
            event["payload"]
            for event in events
            if event["event_type"] == "assistant.message.projection_reset"
        ]
        assert [item["reason"] for item in resets] == ["tool_call_preamble"]
        assert trace["steps"][1]["public_summary"] == "Code task routed to Kimi K2.7 Code"
        assert all(step["status"] == "succeeded" for step in trace["steps"])
    finally:
        service.close()
