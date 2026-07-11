from __future__ import annotations

from collections.abc import Iterator

from fairy_core.assistant.tools import ToolResult
from fairy_core.commanding.registry import ToolDefinition
from fairy_core.domain.models import ScopeContract
from fairy_core.providers import (
    CancellationToken,
    ModelDelta,
    ModelRequest,
    ProviderCapability,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderKind,
    ProviderProfile,
)


class ScriptedProvider:
    def __init__(
        self,
        rounds: list[tuple[ModelDelta, ...]],
        *,
        cancel_after_first_delta: bool = False,
        capabilities: frozenset[ProviderCapability] | None = None,
    ) -> None:
        self.profile = ProviderProfile.create(
            profile_id="scripted",
            display_name="Scripted",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            model_id="scripted-model",
            capabilities=capabilities
            or frozenset({ProviderCapability.TEXT, ProviderCapability.TOOLS}),
            credential_ref=None,
            fallback_profile_id=None,
            timeout_seconds=30,
            enabled=True,
        )
        self.credential_configured = True
        self.rounds = rounds
        self.requests: list[ModelRequest] = []
        self.observed_image_bytes: list[bytes] = []
        self.cancel_after_first_delta = cancel_after_first_delta

    def health(self) -> ProviderHealth:
        return ProviderHealth.create(
            profile_id=self.profile.id,
            status=ProviderHealthStatus.AVAILABLE,
            error_code=None,
            diagnostics=(),
        )

    def stream(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> Iterator[ModelDelta]:
        self.requests.append(request)
        self.observed_image_bytes.extend(
            bytes(image.data) for message in request.messages for image in message.images
        )
        scripted = self.rounds.pop(0)
        for index, delta in enumerate(scripted):
            yield delta
            if self.cancel_after_first_delta and index == 0:
                cancellation.cancel()


class RecordingToolExecutor:
    def __init__(self, *, summary: str = "Tool result") -> None:
        self.summary = summary
        self.calls: list[tuple[ToolDefinition, ScopeContract, dict[str, object]]] = []

    def execute(
        self,
        definition: ToolDefinition,
        scope: ScopeContract,
        arguments: dict[str, object],
    ) -> ToolResult:
        self.calls.append((definition, scope, arguments))
        return ToolResult.create(
            public_summary=self.summary,
            model_content=self.summary,
            artifact_ids=(),
        )
