from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from threading import Event
from time import monotonic, sleep

from fairy_core.application.ambient_dialogue_service import AmbientDialogueService
from fairy_core.commanding.local_device_bus import LocalDeviceCommandBus
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.persona import AmbientDialogueEvaluateInput
from fairy_core.persona import (
    AmbientContextSnapshot,
    AmbientDialogueGenerator,
    AmbientSurface,
    FairyDialogueDirector,
    GeneratedDialogueCandidate,
    TruthSafetyGate,
    load_default_dialogue_catalog,
    load_default_persona_authority,
)
from fairy_core.providers import (
    CancellationToken,
    ModelDelta,
    ModelRequest,
    ProviderCapability,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderKind,
    ProviderNetworkError,
    ProviderProfile,
    ProviderRegistry,
)


class _Provider:
    def __init__(self, outcomes: list[ModelDelta | Exception]) -> None:
        self.profile = ProviderProfile.create(
            profile_id="ambient-free",
            display_name="Ambient Free",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://openrouter.ai/api/v1",
            model_id="nvidia/nemotron-3-ultra-550b-a55b:free",
            capabilities=frozenset({ProviderCapability.TEXT}),
            credential_ref="openrouter",
            fallback_profile_id=None,
            timeout_seconds=30,
            enabled=True,
        )
        self.credential_configured = True
        self.outcomes = outcomes
        self.calls = 0
        self.requests: list[ModelRequest] = []
        self.started = Event()

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
        self.calls += 1
        self.requests.append(request)
        self.started.set()
        for outcome in self.outcomes:
            cancellation.raise_if_cancelled()
            if isinstance(outcome, Exception):
                raise outcome
            yield outcome


def _request(
    *,
    generated: bool,
    observed_at: datetime | None = None,
    state: dict[str, object] | None = None,
) -> AmbientDialogueEvaluateInput:
    return AmbientDialogueEvaluateInput.model_validate(
        {
            "context": {
                "observed_at": observed_at or datetime(2026, 7, 23, 9, 0, tzinfo=UTC),
                "locale": "en",
                "surface": "pet",
                "user_idle_seconds": 900,
            },
            "preferences": {"generated_enabled": generated},
            "state": state or {},
        }
    )


def _service(provider: _Provider) -> AmbientDialogueService:
    registry = build_default_registry()
    persona = load_default_persona_authority()
    return AmbientDialogueService(
        FairyDialogueDirector(
            catalog=load_default_dialogue_catalog(),
            persona=persona,
        ),
        AmbientDialogueGenerator(
            providers=ProviderRegistry((provider,)),
            command_bus=LocalDeviceCommandBus(registry),
            persona=persona,
        ),
    )


def _await_resolution(
    service: AmbientDialogueService,
    *,
    state: dict[str, object],
    observed_at: datetime,
) -> object:
    deadline = monotonic() + 1
    while monotonic() < deadline:
        result = service.evaluate(_request(generated=True, observed_at=observed_at, state=state))
        if result.reason != "generation_pending":
            return result
        sleep(0.005)
    raise AssertionError("ambient generation did not resolve")


def test_generated_dialogue_uses_one_toolless_historyless_free_model_attempt() -> None:
    body = (
        '{"text":"The quiet interval remains under control. Naturally.",'
        '"intent":"idle_long","required_facts":[],"safe_for_tts":true,'
        '"cooldown_group":"generated_idle"}'
    )
    provider = _Provider(
        [
            ModelDelta.text(profile_id="ambient-free", sequence=1, text=body),
            ModelDelta.done(profile_id="ambient-free", sequence=2, finish_reason="stop"),
        ]
    )

    service = _service(provider)
    pending = service.evaluate(_request(generated=True))
    assert pending.projection is None
    assert pending.reason == "generation_pending"
    result = _await_resolution(
        service,
        state=pending.next_state.model_dump(),
        observed_at=datetime(2026, 7, 23, 9, 0, 15, tzinfo=UTC),
    )

    assert provider.calls == 1
    assert result.projection is not None
    assert result.projection.source == "generated_original"
    assert result.projection.text == "The quiet interval remains under control. Naturally."
    assert result.generation_request is None
    assert result.next_state.daily_generated_count == 1
    model_request = provider.requests[0]
    assert model_request.tools == ()
    assert model_request.max_output_tokens == 160
    assert model_request.allow_profile_fallback is False
    assert len(model_request.messages) == 2
    assert "workspace" not in model_request.messages[1].content.casefold()
    assert "history" not in model_request.messages[1].content.casefold()


def test_generated_dialogue_is_zero_call_by_default() -> None:
    provider = _Provider([])

    result = _service(provider).evaluate(_request(generated=False))

    assert provider.calls == 0
    assert result.projection is not None
    assert result.projection.source == "authored_original"


def test_generation_failure_does_not_retry_and_falls_back_to_reviewed_catalog() -> None:
    provider = _Provider([ProviderNetworkError("offline")])

    service = _service(provider)
    pending = service.evaluate(_request(generated=True))
    result = _await_resolution(
        service,
        state=pending.next_state.model_dump(),
        observed_at=datetime(2026, 7, 23, 9, 0, 15, tzinfo=UTC),
    )

    assert provider.calls == 1
    assert result.projection is not None
    assert result.projection.source == "authored_original"
    assert result.generation_request is None
    assert result.next_state.daily_generated_count == 1


def test_failed_generation_attempts_consume_the_two_call_daily_budget() -> None:
    provider = _Provider([ProviderNetworkError("offline")])
    service = _service(provider)
    first = service.evaluate(_request(generated=True))
    first_resolved = _await_resolution(
        service,
        state=first.next_state.model_dump(),
        observed_at=datetime(2026, 7, 23, 9, 0, 15, tzinfo=UTC),
    )
    second = service.evaluate(
        _request(
            generated=True,
            observed_at=datetime(2026, 7, 23, 9, 31, tzinfo=UTC),
            state=first_resolved.next_state.model_dump(),
        )
    )
    second_resolved = _await_resolution(
        service,
        state=second.next_state.model_dump(),
        observed_at=datetime(2026, 7, 23, 9, 31, 15, tzinfo=UTC),
    )
    third = service.evaluate(
        _request(
            generated=True,
            observed_at=datetime(2026, 7, 23, 10, 2, tzinfo=UTC),
            state=second_resolved.next_state.model_dump(),
        )
    )

    assert provider.calls == 2
    assert second.next_state.daily_generated_count == 2
    assert third.next_state.daily_generated_count == 2
    assert third.generation_request is None


def test_generated_dialogue_never_blocks_the_local_core_rpc_loop() -> None:
    release = Event()

    class BlockingProvider(_Provider):
        def stream(
            self,
            request: ModelRequest,
            cancellation: CancellationToken,
        ) -> Iterator[ModelDelta]:
            self.calls += 1
            self.requests.append(request)
            self.started.set()
            release.wait(timeout=2)
            cancellation.raise_if_cancelled()
            raise ProviderNetworkError("offline")

    provider = BlockingProvider([])
    service = _service(provider)
    started_at = monotonic()
    try:
        result = service.evaluate(_request(generated=True))
        elapsed = monotonic() - started_at
        assert provider.started.wait(timeout=0.2)
        assert elapsed < 0.2
        assert result.reason == "generation_pending"
        assert result.projection is None
    finally:
        release.set()


def test_generated_dialogue_rejects_customer_service_and_servant_drift() -> None:
    context = AmbientContextSnapshot(
        observed_at=datetime(2026, 7, 23, 9, 0, tzinfo=UTC),
        locale="zh-CN",
        surface=AmbientSurface.PET,
        user_idle_seconds=900,
        startup_eligible=False,
        user_returned=False,
        network_restored=False,
        battery_percent=None,
        charging=None,
        charging_started=False,
        locked=False,
        do_not_disturb=False,
        typing=False,
        input_open=False,
        microphone_active=False,
        fullscreen=False,
        realtime_active=False,
        active_turn=False,
        approval_waiting=False,
        severe_error=False,
        tts_active=False,
    )
    gate = TruthSafetyGate()
    persona = load_default_persona_authority()

    for text in (
        "主人主人, 很高兴为您服务。",
        "我是你最听话的女仆, 请尽情吩咐。",
    ):
        result = gate.validate(
            GeneratedDialogueCandidate(
                text=text,
                intent="idle_long",
                required_facts=(),
                safe_for_tts=True,
                cooldown_group="generated_idle",
            ),
            context=context,
            persona=persona,
        )
        assert result.accepted is False
        assert result.reason == "persona_drift"
