from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fairy_core.assistant.routing import DEEPSEEK_MODEL_ID, GLM_MODEL_ID
from fairy_core.model_catalog.models import (
    MODEL_ALLOWLIST,
    ModelAvailability,
    ModelCatalogEntry,
    ModelEndpointKind,
    ModelPrice,
    ProviderCredentialStatus,
)
from fairy_core.model_catalog.ports import ModelCatalogFetchResult
from fairy_core.providers import (
    ModelDelta,
    ModelExecutionRole,
    ProviderCapability,
    ProviderRegistry,
)
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider


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
        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
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

        assert completed["status"] == "completed"
        assert completed["routing_decision"]["primary_model_id"] == GLM_MODEL_ID
        assert completed["routing_decision"]["reviewer_model_id"] == DEEPSEEK_MODEL_ID
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
        assert deepseek.requests[-1].messages[-2].content == "Unreviewed private draft."
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
