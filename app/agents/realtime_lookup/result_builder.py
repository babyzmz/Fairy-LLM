"""Result Rendering Layer — Stage 3.

Builds unified RealtimeResult from provider data or snippet extraction.
Card protocol is strictly data-only; UI handles animation and layout.

Card types: weather_card, time_card, crypto_card, stock_card,
            fx_card, fuel_card, sports_card, event_card, generic_card
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RealtimeResult:
    """Unified result returned by the lookup engine."""

    success: bool
    speech_text: str              # TTS-friendly plain text
    card_payload: dict[str, Any]  # Structured card for UI
    numeric_value: float | None   # Primary numeric value (for validation)
    confidence: float             # 0.0 – 1.0
    source_urls: list[str] = field(default_factory=list)
    stage_used: str = ""          # Which stage produced this result
    subtype: str = ""
    error_message: str = ""

    @property
    def is_failure(self) -> bool:
        return not self.success


class ResultBuilder:
    """Build RealtimeResult from raw provider / snippet data."""

    # ------------------------------------------------------------------
    # Weather
    # ------------------------------------------------------------------
    @staticmethod
    def weather(
        location: str,
        data: dict[str, Any],
        confidence: float = 0.95,
        stage: str = "stage1",
        source_urls: list[str] | None = None,
    ) -> RealtimeResult:
        temp   = data.get("temperature_c")
        cond   = data.get("condition", "")
        humid  = data.get("humidity_pct")
        wind   = data.get("wind_kmh")
        feels  = data.get("feels_like_c")

        temp_str = f"{temp:.0f}°C" if temp is not None else "N/A"
        parts = [f"{location}: {temp_str}"]
        if cond:
            parts.append(cond)
        if humid is not None:
            parts.append(f"湿度 {humid}%")
        if wind is not None:
            parts.append(f"风速 {wind:.0f}km/h")

        secondary = []
        if humid is not None:
            secondary.append(f"Humidity {humid}%")
        if wind is not None:
            secondary.append(f"Wind {wind:.0f}km/h")
        if feels is not None:
            secondary.append(f"Feels like {feels:.0f}°C")

        card = {
            "type":       "weather_card",
            "title":      f"{location} Weather",
            "primary":    temp_str,
            "secondary":  secondary,
            "condition":  cond,
            "timestamp":  datetime.utcnow().isoformat() + "Z",
            "confidence": round(confidence, 2),
            # Legacy compat fields
            "card_type":  "weather",
            "city":       location,
            "temperature": temp,
        }
        return RealtimeResult(
            success=True,
            speech_text=", ".join(parts),
            card_payload=card,
            numeric_value=temp,
            confidence=confidence,
            source_urls=source_urls or [],
            stage_used=stage,
            subtype="weather",
        )

    # ------------------------------------------------------------------
    # Time
    # ------------------------------------------------------------------
    @staticmethod
    def time(
        location: str,
        data: dict[str, Any],
        confidence: float = 0.99,
        stage: str = "stage1",
    ) -> RealtimeResult:
        date    = data.get("date", "")
        time_s  = data.get("time", "")
        weekday = data.get("weekday", "")
        period  = data.get("period_zh", "")
        tz_name = data.get("tz_name", "")
        hour    = data.get("hour", 0)
        is_day  = data.get("is_daytime", True)

        speech = f"{location}: {date} {time_s} {period} ({weekday})"
        card = {
            "type":       "time_card",
            "title":      f"{location} 当前时间",
            "primary":    time_s,
            "secondary":  [date, weekday, period],
            "timezone":   tz_name,
            "is_daytime": is_day,
            "timestamp":  datetime.utcnow().isoformat() + "Z",
            "confidence": round(confidence, 2),
            # Legacy compat
            "card_type":  "time",
            "city":       location,
            "date":       date,
            "time":       time_s,
            "period":     period,
        }
        return RealtimeResult(
            success=True,
            speech_text=speech,
            card_payload=card,
            numeric_value=float(hour),
            confidence=confidence,
            stage_used=stage,
            subtype="time",
        )

    # ------------------------------------------------------------------
    # Crypto
    # ------------------------------------------------------------------
    @staticmethod
    def crypto(
        symbol: str,
        data: dict[str, Any],
        confidence: float = 0.92,
        stage: str = "stage1",
        source_urls: list[str] | None = None,
    ) -> RealtimeResult:
        price  = data.get("price_usd")
        change = data.get("change_24h_pct")

        price_str  = f"${price:,.2f}" if price is not None else "N/A"
        change_str = f"{change:+.2f}%" if change is not None else ""
        speech = f"{symbol}: {price_str} USD"
        if change_str:
            speech += f" ({change_str} 24h)"

        secondary = []
        if change is not None:
            secondary.append(f"24h: {change_str}")

        card = {
            "type":       "crypto_card",
            "title":      f"{symbol} Price",
            "primary":    price_str,
            "secondary":  secondary,
            "timestamp":  datetime.utcnow().isoformat() + "Z",
            "confidence": round(confidence, 2),
            # Legacy compat
            "card_type":  "crypto",
            "symbol":     symbol,
            "price":      price,
            "change_24h": change,
            "currency":   "USD",
        }
        return RealtimeResult(
            success=True,
            speech_text=speech,
            card_payload=card,
            numeric_value=price,
            confidence=confidence,
            source_urls=source_urls or [],
            stage_used=stage,
            subtype="crypto",
        )

    # ------------------------------------------------------------------
    # Stock
    # ------------------------------------------------------------------
    @staticmethod
    def stock(
        symbol: str,
        data: dict[str, Any],
        confidence: float = 0.90,
        stage: str = "stage1",
        source_urls: list[str] | None = None,
    ) -> RealtimeResult:
        price   = data.get("price_usd")
        change  = data.get("change_pct")
        company = data.get("company", symbol)

        price_str  = f"${price:,.2f}" if price is not None else "N/A"
        change_str = f"{change:+.2f}%" if change is not None else ""
        speech = f"{company} ({symbol}): {price_str}"
        if change_str:
            speech += f" {change_str}"

        secondary = [company] if company != symbol else []
        if change is not None:
            secondary.append(f"Change: {change_str}")

        card = {
            "type":       "stock_card",
            "title":      f"{symbol} Stock",
            "primary":    price_str,
            "secondary":  secondary,
            "timestamp":  datetime.utcnow().isoformat() + "Z",
            "confidence": round(confidence, 2),
            # Legacy compat
            "card_type":     "stock",
            "symbol":        symbol,
            "company":       company,
            "price":         price,
            "change_percent": change,
            "currency":      "USD",
        }
        return RealtimeResult(
            success=True,
            speech_text=speech,
            card_payload=card,
            numeric_value=price,
            confidence=confidence,
            source_urls=source_urls or [],
            stage_used=stage,
            subtype="stock",
        )

    # ------------------------------------------------------------------
    # FX
    # ------------------------------------------------------------------
    @staticmethod
    def fx(
        data: dict[str, Any],
        confidence: float = 0.92,
        stage: str = "stage1",
        source_urls: list[str] | None = None,
    ) -> RealtimeResult:
        frm  = data.get("from_currency", "")
        to   = data.get("to_currency", "")
        rate = data.get("rate")

        rate_str = f"{rate:.4f}" if rate is not None else "N/A"
        speech = f"1 {frm} = {rate_str} {to}"

        card = {
            "type":          "fx_card",
            "title":         f"{frm}/{to} Rate",
            "primary":       rate_str,
            "secondary":     [f"1 {frm} → {to}"],
            "timestamp":     datetime.utcnow().isoformat() + "Z",
            "confidence":    round(confidence, 2),
            # Legacy compat
            "card_type":     "exchange",
            "from_currency": frm,
            "to_currency":   to,
            "rate":          rate,
        }
        return RealtimeResult(
            success=True,
            speech_text=speech,
            card_payload=card,
            numeric_value=rate,
            confidence=confidence,
            source_urls=source_urls or [],
            stage_used=stage,
            subtype="exchange",
        )

    # ------------------------------------------------------------------
    # Fuel
    # ------------------------------------------------------------------
    @staticmethod
    def fuel(
        location: str,
        fuel_type: str,
        price_min: float | None,
        price_max: float | None,
        confidence: float = 0.80,
        stage: str = "stage2",
        source_urls: list[str] | None = None,
    ) -> RealtimeResult:
        if price_min is not None and price_max is not None and price_min != price_max:
            primary = f"{price_min:.2f}–{price_max:.2f} AUD/L"
            numeric = (price_min + price_max) / 2
        elif price_min is not None:
            primary = f"{price_min:.2f} AUD/L"
            numeric = price_min
        else:
            primary = "N/A"
            numeric = None

        speech = f"{location} {fuel_type}: {primary}"
        card = {
            "type":       "fuel_card",
            "title":      f"{location} {fuel_type} Price",
            "primary":    primary,
            "secondary":  [f"Fuel type: {fuel_type}"],
            "timestamp":  datetime.utcnow().isoformat() + "Z",
            "confidence": round(confidence, 2),
            # Legacy compat
            "card_type":  "fuel",
            "location":   location,
            "fuel_type":  fuel_type,
            "price_min":  price_min,
            "price_max":  price_max,
            "currency":   "AUD/L",
        }
        return RealtimeResult(
            success=True,
            speech_text=speech,
            card_payload=card,
            numeric_value=numeric,
            confidence=confidence,
            source_urls=source_urls or [],
            stage_used=stage,
            subtype="fuel",
        )

    # ------------------------------------------------------------------
    # Failure
    # ------------------------------------------------------------------
    @staticmethod
    def failure(
        subtype: str = "",
        error_message: str = "实时数据暂不可用",
        stage: str = "",
    ) -> RealtimeResult:
        return RealtimeResult(
            success=False,
            speech_text=error_message,
            card_payload={"type": "error_card", "message": error_message},
            numeric_value=None,
            confidence=0.0,
            stage_used=stage,
            subtype=subtype,
            error_message=error_message,
        )
