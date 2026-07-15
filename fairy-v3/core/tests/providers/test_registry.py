from __future__ import annotations

from collections.abc import Iterator

import pytest

from fairy_core.providers.models import (
    ModelDelta,
    ModelDeltaKind,
    ModelMessage,
    ModelRequest,
    ModelRole,
    ProviderCapability,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderKind,
    ProviderProfile,
)
from fairy_core.providers.ports import (
    CancellationToken,
    ProviderCancelledError,
    ProviderProtocolError,
    ProviderUnavailableError,
)
from fairy_core.providers.registry import ProviderRegistry


class FakeProvider:
    def __init__(
        self,
        profile: ProviderProfile,
        outcomes: list[ModelDelta | Exception],
        *,
        credential_configured: bool = True,
    ) -> None:
        self.profile = profile
        self.credential_configured = credential_configured
        self.outcomes = outcomes
        self.calls = 0

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
        for outcome in self.outcomes:
            cancellation.raise_if_cancelled()
            if isinstance(outcome, Exception):
                raise outcome
            yield outcome


def _profile(
    profile_id: str,
    *,
    fallback: str | None = None,
    capabilities: frozenset[ProviderCapability] | None = None,
) -> ProviderProfile:
    return ProviderProfile.create(
        profile_id=profile_id,
        display_name=profile_id,
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url=f"https://{profile_id}.example.test/v1",
        model_id="model",
        capabilities=capabilities or frozenset({ProviderCapability.TEXT}),
        credential_ref=f"secret-{profile_id}",
        fallback_profile_id=fallback,
        timeout_seconds=30,
        enabled=True,
    )


def _request(profile_id: str) -> ModelRequest:
    return ModelRequest.create(
        profile_id=profile_id,
        messages=(ModelMessage.create(role=ModelRole.USER, content="Hello"),),
        tools=(),
        required_capabilities=frozenset({ProviderCapability.TEXT}),
        max_output_tokens=256,
    )


def _text(profile_id: str, sequence: int, value: str) -> ModelDelta:
    return ModelDelta.text(profile_id=profile_id, sequence=sequence, text=value)


def test_registry_requires_unique_profiles_and_compatible_explicit_fallback() -> None:
    primary = FakeProvider(_profile("primary", fallback="fallback"), [])
    incomplete_fallback = FakeProvider(
        _profile("fallback", capabilities=frozenset({ProviderCapability.TEXT})),
        [],
    )
    tools_primary = FakeProvider(
        _profile(
            "tools-primary",
            fallback="fallback",
            capabilities=frozenset({ProviderCapability.TEXT, ProviderCapability.TOOLS}),
        ),
        [],
    )

    with pytest.raises(ValueError, match="duplicate"):
        ProviderRegistry((primary, primary))
    with pytest.raises(ValueError, match="fallback"):
        ProviderRegistry((primary,))
    with pytest.raises(ValueError, match="capabilities"):
        ProviderRegistry((tools_primary, incomplete_fallback))


def test_registry_falls_back_only_before_the_first_delta() -> None:
    primary = FakeProvider(
        _profile("primary", fallback="fallback"),
        [ProviderUnavailableError("primary unavailable")],
    )
    fallback = FakeProvider(
        _profile("fallback"),
        [_text("fallback", 1, "Recovered")],
    )
    registry = ProviderRegistry((primary, fallback))

    deltas = tuple(registry.stream(_request("primary"), CancellationToken()))

    assert [(delta.profile_id, delta.text) for delta in deltas] == [("fallback", "Recovered")]
    assert primary.calls == 2
    assert fallback.calls == 1


def test_registry_honors_request_scoped_fallback_without_static_profile_link() -> None:
    primary = FakeProvider(
        _profile("primary"),
        [ProviderUnavailableError("primary unavailable")],
    )
    fallback = FakeProvider(
        _profile("fallback"),
        [_text("fallback", 1, "Recovered")],
    )
    request = ModelRequest.create(
        profile_id="primary",
        messages=(ModelMessage.create(role=ModelRole.USER, content="Hello"),),
        tools=(),
        required_capabilities=frozenset({ProviderCapability.TEXT}),
        max_output_tokens=256,
        fallback_profile_ids=("fallback",),
    )

    deltas = tuple(ProviderRegistry((primary, fallback)).stream(request, CancellationToken()))

    assert [(delta.profile_id, delta.text) for delta in deltas] == [("fallback", "Recovered")]
    assert primary.calls == 2
    assert fallback.calls == 1


def test_registry_can_disable_a_static_profile_fallback_for_manual_selection() -> None:
    primary = FakeProvider(
        _profile("primary", fallback="fallback"),
        [ProviderUnavailableError("primary unavailable")],
    )
    fallback = FakeProvider(
        _profile("fallback"),
        [_text("fallback", 1, "Must not be used")],
    )
    request = ModelRequest.create(
        profile_id="primary",
        messages=(ModelMessage.create(role=ModelRole.USER, content="Hello"),),
        tools=(),
        required_capabilities=frozenset({ProviderCapability.TEXT}),
        max_output_tokens=256,
        allow_profile_fallback=False,
    )

    with pytest.raises(ProviderUnavailableError, match="primary unavailable"):
        tuple(ProviderRegistry((primary, fallback)).stream(request, CancellationToken()))

    assert primary.calls == 2
    assert fallback.calls == 0


def test_registry_reports_each_retry_and_fallback_attempt() -> None:
    primary = FakeProvider(
        _profile("primary", fallback="fallback"),
        [ProviderUnavailableError("primary unavailable")],
    )
    fallback = FakeProvider(_profile("fallback"), [_text("fallback", 1, "Recovered")])
    events = []

    result = tuple(
        ProviderRegistry((primary, fallback)).stream(
            _request("primary"),
            CancellationToken(),
            on_attempt=events.append,
        )
    )

    assert result[0].text == "Recovered"
    assert [event.status.value for event in events] == [
        "started",
        "failed",
        "started",
        "failed",
        "started",
        "succeeded",
    ]
    assert [event.profile_id for event in events] == [
        "primary",
        "primary",
        "primary",
        "primary",
        "fallback",
        "fallback",
    ]
    assert events[1].error_category.value == "unavailable"

    partial = FakeProvider(
        _profile("partial", fallback="fallback"),
        [_text("partial", 1, "Partial"), ProviderUnavailableError("stream broke")],
    )
    partial_registry = ProviderRegistry((partial, fallback))
    stream = partial_registry.stream(_request("partial"), CancellationToken())
    assert next(stream).text == "Partial"
    with pytest.raises(ProviderUnavailableError, match="stream broke"):
        next(stream)
    assert fallback.calls == 1


def test_registry_never_falls_back_after_cancellation() -> None:
    token = CancellationToken()
    token.cancel()
    primary = FakeProvider(_profile("primary", fallback="fallback"), [])
    fallback = FakeProvider(_profile("fallback"), [_text("fallback", 1, "wrong")])
    registry = ProviderRegistry((primary, fallback))

    with pytest.raises(ProviderCancelledError):
        tuple(registry.stream(_request("primary"), token))
    assert primary.calls == 0
    assert fallback.calls == 0


def test_registry_public_profiles_hide_secret_references() -> None:
    provider = FakeProvider(
        _profile("primary"),
        [_text("primary", 1, "ok")],
        credential_configured=False,
    )
    public = ProviderRegistry((provider,)).list_public()

    assert len(public) == 1
    assert public[0].credential_required is True
    assert public[0].credential_configured is False
    assert not hasattr(public[0], "credential_ref")
    assert ModelDeltaKind.TEXT.value == "text"


@pytest.mark.parametrize(
    "outcomes",
    [
        [_text("wrong-profile", 1, "wrong")],
        [_text("primary", 2, "starts late")],
        [_text("primary", 1, "first"), _text("primary", 1, "duplicate")],
        [_text("primary", 1, "first"), _text("primary", 3, "gap")],
    ],
)
def test_registry_rejects_untrusted_profile_and_sequence_metadata(
    outcomes: list[ModelDelta],
) -> None:
    registry = ProviderRegistry((FakeProvider(_profile("primary"), outcomes),))

    with pytest.raises(ProviderProtocolError):
        tuple(registry.stream(_request("primary"), CancellationToken()))


def test_registry_validates_fallback_delta_metadata() -> None:
    primary = FakeProvider(
        _profile("primary", fallback="fallback"),
        [ProviderUnavailableError("primary unavailable")],
    )
    fallback = FakeProvider(
        _profile("fallback"),
        [_text("primary", 1, "forged fallback identity")],
    )
    registry = ProviderRegistry((primary, fallback))

    with pytest.raises(ProviderProtocolError):
        tuple(registry.stream(_request("primary"), CancellationToken()))
