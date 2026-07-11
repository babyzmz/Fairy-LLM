from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fairy_core.domain.urls import canonical_http_url

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,15}$")


class UnitSystem(StrEnum):
    METRIC = "metric"
    IMPERIAL = "imperial"


class InformationCapabilityStatus(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class StrictInformationModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class InformationCapabilityHealth(StrictInformationModel):
    provider: str = Field(min_length=1, max_length=128)
    status: InformationCapabilityStatus
    observed_at: datetime
    error_code: str | None = Field(default=None, max_length=128)
    diagnostics: tuple[str, ...] = ()

    @field_validator("observed_at")
    @classmethod
    def aware_observed_at(cls, value: datetime) -> datetime:
        return _aware(value, "observed_at")

    @field_validator("provider")
    @classmethod
    def canonical_provider(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("health provider must be canonical")
        return value

    @field_validator("diagnostics")
    @classmethod
    def bounded_diagnostics(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 16 or any(not value.strip() or len(value) > 512 for value in values):
            raise ValueError("health diagnostics are invalid")
        return values

    @model_validator(mode="after")
    def validate_status(self) -> InformationCapabilityHealth:
        if (self.status is InformationCapabilityStatus.AVAILABLE) != (self.error_code is None):
            raise ValueError("non-available health requires an error code")
        return self


class InformationResult(StrictInformationModel):
    provider: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    freshness: str = Field(min_length=1, max_length=128)
    source_url: str = Field(min_length=1, max_length=2_048)
    diagnostics: tuple[str, ...] = ()

    @field_validator("observed_at")
    @classmethod
    def aware_observed_at(cls, value: datetime) -> datetime:
        return _aware(value, "observed_at")

    @field_validator("source_url")
    @classmethod
    def canonical_source_url(cls, value: str) -> str:
        return canonical_http_url(value)

    @field_validator("provider", "freshness")
    @classmethod
    def normalized_text(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != value:
            raise ValueError("information metadata text must be canonical")
        return value

    @field_validator("diagnostics")
    @classmethod
    def bounded_diagnostics(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 16:
            raise ValueError("too many information diagnostics")
        for value in values:
            if not value.strip() or len(value) > 512:
                raise ValueError("information diagnostic is invalid")
        return values


class LocationCandidate(StrictInformationModel):
    provider_id: int
    name: str = Field(min_length=1, max_length=255)
    country: str = Field(min_length=1, max_length=255)
    country_code: str
    admin1: str | None = Field(default=None, max_length=255)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone: str = Field(min_length=1, max_length=255)

    @field_validator("country_code")
    @classmethod
    def country_code_is_iso_alpha2(cls, value: str) -> str:
        if _COUNTRY.fullmatch(value) is None:
            raise ValueError("country_code must be ISO alpha-2")
        return value


class WeatherResult(InformationResult):
    location: LocationCandidate
    unit_system: UnitSystem
    data_time: datetime
    temperature: float
    temperature_unit: str = Field(min_length=1, max_length=32)
    apparent_temperature: float
    relative_humidity: int = Field(ge=0, le=100)
    precipitation: float = Field(ge=0)
    precipitation_unit: str = Field(min_length=1, max_length=32)
    weather_code: int = Field(ge=0, le=999)
    wind_speed: float = Field(ge=0)
    wind_speed_unit: str = Field(min_length=1, max_length=32)

    @field_validator("data_time")
    @classmethod
    def aware_data_time(cls, value: datetime) -> datetime:
        return _aware(value, "data_time")


class TimeResult(InformationResult):
    timezone: str = Field(min_length=1, max_length=255)
    local_time: datetime
    utc_offset_seconds: int = Field(ge=-86_400, le=86_400)
    is_dst: bool

    @field_validator("local_time")
    @classmethod
    def aware_local_time(cls, value: datetime) -> datetime:
        return _aware(value, "local_time")


class MapResult(InformationResult):
    query: str = Field(min_length=1, max_length=1_000)


class FxResult(InformationResult):
    base_currency: str
    quote_currency: str
    amount: float = Field(ge=0)
    rate: float = Field(gt=0)
    converted_amount: float = Field(ge=0)
    rate_date: date

    @field_validator("base_currency", "quote_currency")
    @classmethod
    def currency_is_iso_4217(cls, value: str) -> str:
        if _CURRENCY.fullmatch(value) is None:
            raise ValueError("currency must be an uppercase ISO-4217 code")
        return value


class StockResult(InformationResult):
    symbol: str
    currency: str | None = None
    price: float = Field(ge=0)
    previous_close: float = Field(ge=0)
    change: float
    change_percent: float
    trading_day: date
    delayed: bool

    @field_validator("symbol")
    @classmethod
    def stock_symbol_is_valid(cls, value: str) -> str:
        return _symbol(value)

    @field_validator("currency")
    @classmethod
    def optional_currency_is_valid(cls, value: str | None) -> str | None:
        if value is not None and _CURRENCY.fullmatch(value) is None:
            raise ValueError("currency must be an uppercase ISO-4217 code")
        return value


class CryptoResult(InformationResult):
    symbol: str
    market_currency: str
    close: float = Field(ge=0)
    volume: float = Field(ge=0)
    trading_day: date

    @field_validator("symbol")
    @classmethod
    def crypto_symbol_is_valid(cls, value: str) -> str:
        return _symbol(value)

    @field_validator("market_currency")
    @classmethod
    def market_currency_is_valid(cls, value: str) -> str:
        if _CURRENCY.fullmatch(value) is None:
            raise ValueError("market_currency must be an uppercase ISO-4217 code")
        return value


class NewsItem(StrictInformationModel):
    rank: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=1_000)
    url: str = Field(min_length=1, max_length=2_048)
    description: str = Field(max_length=4_000)
    published_at: datetime | None = None
    source: str | None = Field(default=None, max_length=255)

    @field_validator("url")
    @classmethod
    def canonical_url(cls, value: str) -> str:
        return canonical_http_url(value)

    @field_validator("published_at")
    @classmethod
    def aware_published_at(cls, value: datetime | None) -> datetime | None:
        return _aware(value, "published_at") if value is not None else None


class NewsResult(InformationResult):
    query: str = Field(min_length=1, max_length=2_000)
    items: tuple[NewsItem, ...]


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _symbol(value: str) -> str:
    if _SYMBOL.fullmatch(value) is None:
        raise ValueError("symbol is invalid")
    return value


__all__ = [
    "CryptoResult",
    "FxResult",
    "InformationCapabilityHealth",
    "InformationCapabilityStatus",
    "LocationCandidate",
    "MapResult",
    "NewsItem",
    "NewsResult",
    "StockResult",
    "TimeResult",
    "UnitSystem",
    "WeatherResult",
]
