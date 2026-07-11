from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fairy_core.assistant.tools import ToolResult
from fairy_core.commanding.registry import build_default_registry
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import OperationMode, ScopeContract, WorkspaceType
from fairy_core.information import (
    CryptoResult,
    FxResult,
    LocationCandidate,
    StockResult,
    UnitSystem,
    WeatherResult,
)
from fairy_core.research import (
    ResearchCapabilityHealth,
    ResearchCapabilityStatus,
    SearchHit,
    SearchRequest,
)

from fairy_capabilities.information.open_meteo import AmbiguousLocationError
from fairy_capabilities.information.timezones import TimeZoneService
from fairy_capabilities.information.tools import InformationToolExecutor

AT = datetime(2026, 7, 11, tzinfo=UTC)
LOCATION = LocationCandidate(
    provider_id=1,
    name="Sydney",
    country="Australia",
    country_code="AU",
    admin1="New South Wales",
    latitude=-33.8688,
    longitude=151.2093,
    timezone="Australia/Sydney",
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


class _Weather:
    def current_weather(self, **_arguments) -> WeatherResult:
        return WeatherResult(
            provider="open_meteo",
            observed_at=AT,
            freshness="current_15_minute_model",
            source_url="https://api.open-meteo.com/v1/forecast",
            diagnostics=(),
            location=LOCATION,
            unit_system=UnitSystem.METRIC,
            data_time=AT,
            temperature=16.5,
            temperature_unit="°C",
            apparent_temperature=15.6,
            relative_humidity=72,
            precipitation=0.0,
            precipitation_unit="mm",
            weather_code=3,
            wind_speed=12.0,
            wind_speed_unit="km/h",
        )


class _AmbiguousWeather:
    def current_weather(self, **_arguments):
        raise AmbiguousLocationError((LOCATION, LOCATION.model_copy(update={"provider_id": 2})))


class _Fx:
    def convert(self, **_arguments) -> FxResult:
        return FxResult(
            provider="frankfurter_v2",
            observed_at=AT,
            freshness="reference_rate_2026-07-10",
            source_url="https://api.frankfurter.dev/v2/rate/USD/EUR",
            diagnostics=(),
            base_currency="USD",
            quote_currency="EUR",
            amount=10.0,
            rate=0.9,
            converted_amount=9.0,
            rate_date=date(2026, 7, 10),
        )


class _Markets:
    def stock(self, _symbol: str) -> StockResult:
        return StockResult(
            provider="alpha_vantage",
            observed_at=AT,
            freshness="delayed_or_last_close_2026-07-10",
            source_url="https://www.alphavantage.co/documentation/",
            diagnostics=(),
            symbol="MSFT",
            currency=None,
            price=496.62,
            previous_close=497.45,
            change=-0.83,
            change_percent=-0.1669,
            trading_day=date(2026, 7, 10),
            delayed=True,
        )

    def crypto(self, **_arguments) -> CryptoResult:
        return CryptoResult(
            provider="alpha_vantage",
            observed_at=AT,
            freshness="daily_close_2026-07-10",
            source_url="https://www.alphavantage.co/documentation/",
            diagnostics=(),
            symbol="BTC",
            market_currency="AUD",
            close=168250.5,
            volume=1234.5,
            trading_day=date(2026, 7, 10),
        )


class _News:
    def health(self) -> ResearchCapabilityHealth:
        return ResearchCapabilityHealth.create(
            provider="fixture",
            status=ResearchCapabilityStatus.AVAILABLE,
            observed_at=AT,
            error_code=None,
        )

    def search(self, request: SearchRequest) -> tuple[SearchHit, ...]:
        assert request.kind.value == "news"
        return (
            SearchHit.create(
                provider="brave_news",
                rank=1,
                title="Fairy released",
                url="https://news.example.com/fairy",
                description="Release notes",
                published_at=AT,
                source="Example News",
            ),
        )


class _Delegate:
    def execute(self, definition, scope, arguments) -> ToolResult:
        del definition, scope, arguments
        return ToolResult.create(
            public_summary="delegated",
            model_content="delegated",
            artifact_ids=(),
        )


def _executor(weather=None) -> InformationToolExecutor:
    return InformationToolExecutor(
        weather=weather or _Weather(),
        timezones=TimeZoneService(clock=lambda: AT),
        fx=_Fx(),
        markets=_Markets(),
        news_search=_News(),
        delegate=_Delegate(),
        clock=lambda: AT,
    )


def test_information_tools_return_source_labelled_normalized_results() -> None:
    registry = build_default_registry()
    executor = _executor()
    scope = _scope("open_web_safe")
    calls = (
        ("info.weather", {"location": "Sydney", "units": "metric"}),
        ("info.news", {"query": "Fairy", "count": 1}),
        ("info.time", {"timezone": "Australia/Sydney"}),
        ("info.map", {"query": "Café & Museum"}),
        ("info.stock", {"symbol": "MSFT"}),
        ("info.fx", {"base": "USD", "quote": "EUR", "amount": 10}),
        ("info.crypto", {"symbol": "BTC", "market_currency": "AUD"}),
    )

    results = [executor.execute(registry.get(name), scope, arguments) for name, arguments in calls]

    assert all("[INFORMATION_RESULT untrusted=true]" in result.model_content for result in results)
    assert all("source_url" in result.model_content for result in results)
    assert "Fairy released" in results[1].model_content
    assert "delayed_or_last_close" in results[4].model_content


def test_remote_information_tools_enforce_core_network_policy() -> None:
    registry = build_default_registry()
    executor = _executor()
    blocked = _scope("off")

    for name, arguments in (
        ("info.weather", {"location": "Sydney"}),
        ("info.news", {"query": "Fairy"}),
        ("info.stock", {"symbol": "MSFT"}),
        ("info.fx", {"base": "USD", "quote": "EUR"}),
        ("info.crypto", {"symbol": "BTC", "market_currency": "AUD"}),
    ):
        with pytest.raises(Exception) as captured:
            executor.execute(registry.get(name), blocked, arguments)
        assert getattr(captured.value, "error_code", None) == "NETWORK_ACCESS_BLOCKED"

    assert executor.execute(
        registry.get("info.time"), blocked, {"timezone": "Australia/Sydney"}
    ).public_summary
    assert executor.execute(registry.get("info.map"), blocked, {"query": "Sydney"}).public_summary


def test_ambiguous_weather_returns_candidates_without_fabricating_weather() -> None:
    result = _executor(_AmbiguousWeather()).execute(
        build_default_registry().get("info.weather"),
        _scope("project_safe"),
        {"location": "Sydney"},
    )

    assert "LOCATION_CANDIDATES" in result.model_content
    assert "candidate_index" in result.model_content
    assert "clarification" in result.public_summary.lower()


def test_non_information_tool_delegates() -> None:
    result = _executor().execute(
        build_default_registry().get("project.read"),
        _scope("off"),
        {},
    )

    assert result.public_summary == "delegated"
