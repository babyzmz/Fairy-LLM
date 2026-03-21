from __future__ import annotations

from typing import Any

from app.ui.components.chat.chat_message import ChatMessage


def build_assistant_chat_message(
    *,
    assistant_text: str,
    assistant_html: str = "",
    payload: dict[str, Any] | None = None,
    language: str = "zh_CN",
) -> ChatMessage:
    response_payload = dict(payload or {})
    structured = response_payload.get("structured")
    sources = response_payload.get("sources")
    if isinstance(structured, dict):
        for builder in (
            _build_weather_message,
            _build_map_message,
            _build_image_message,
            _build_news_message,
            _build_link_message,
            _build_suggestion_message,
        ):
            message = builder(
                structured=structured,
                payload=response_payload,
                assistant_text=assistant_text,
                sources=sources,
                language=language,
            )
            if message is not None:
                return message
    return _build_text_message(assistant_text=assistant_text, assistant_html=assistant_html)


def _build_text_message(*, assistant_text: str, assistant_html: str = "") -> ChatMessage:
    return ChatMessage.create(
        role="assistant",
        message_type="text",
        payload={
            "speaker": "Fairy",
            "text": assistant_html or assistant_text,
            "rich_text": bool(assistant_html),
        },
    )


def _build_weather_message(
    *,
    structured: dict[str, Any],
    payload: dict[str, Any],
    assistant_text: str,
    sources: object,
    language: str,
) -> ChatMessage | None:
    _ = payload, sources, language
    if not (
        structured.get("weather_location")
        or structured.get("weather_label")
        or structured.get("city")
        or structured.get("temp") is not None
    ):
        return None
    city = _clean_text(structured.get("city")) or _clean_text(structured.get("weather_location")) or (
        "Weather" if str(language).startswith("en") else "天气"
    )
    condition = _clean_text(structured.get("condition")) or _clean_text(structured.get("weather_label")) or (
        "Current" if str(language).startswith("en") else "当前"
    )
    return ChatMessage.create(
        role="assistant",
        message_type="weather",
        payload={
            "city": city,
            "temp": _display_number(structured.get("temp")),
            "high": _display_number(structured.get("high")),
            "low": _display_number(structured.get("low")),
            "feels_like": _display_number(structured.get("feels_like")),
            "wind": _clean_text(structured.get("wind")),
            "condition": condition,
            "icon_type": _clean_text(structured.get("icon_type")) or _weather_icon_type(condition),
            "summary": _clean_text(structured.get("summary")) or assistant_text,
            "hourly_curve": _number_list(structured.get("hourly_curve")),
        },
    )


def _build_map_message(
    *,
    structured: dict[str, Any],
    payload: dict[str, Any],
    assistant_text: str,
    sources: object,
    language: str,
) -> ChatMessage | None:
    _ = payload, assistant_text, sources, language
    address = _clean_text(structured.get("address")) or _clean_text(structured.get("title"))
    lat = _float_or_none(structured.get("lat"))
    lon = _float_or_none(structured.get("lon"))
    map_url = _clean_text(structured.get("map_url")) or _clean_text(structured.get("url"))
    if not (address or map_url or (lat is not None and lon is not None)):
        return None
    return ChatMessage.create(
        role="assistant",
        message_type="map",
        payload={
            "address": address or ("Map Location" if str(language).startswith("en") else "地图位置"),
            "distance": _clean_text(structured.get("distance")) or _clean_text(structured.get("subtitle")),
            "image_url": _clean_text(structured.get("image_url")) or _build_static_map_url(lat, lon),
            "url": map_url,
            "lat": lat,
            "lon": lon,
        },
    )


def _build_image_message(
    *,
    structured: dict[str, Any],
    payload: dict[str, Any],
    assistant_text: str,
    sources: object,
    language: str,
) -> ChatMessage | None:
    _ = payload, sources
    image_source = (
        _clean_text(structured.get("image"))
        or _clean_text(structured.get("image_path"))
        or _clean_text(structured.get("image_url"))
        or _clean_text(structured.get("capture_path"))
    )
    if not image_source:
        return None
    current_app = structured.get("current_app") if isinstance(structured.get("current_app"), dict) else {}
    title = _clean_text(current_app.get("app_name")) or (
        "Screen Preview" if str(language).startswith("en") else "屏幕预览"
    )
    caption = (
        _clean_text(structured.get("screen_summary"))
        or _clean_text(structured.get("summary"))
        or _clean_text(structured.get("suggested_next_step"))
        or assistant_text
    )
    return ChatMessage.create(
        role="assistant",
        message_type="image",
        payload={
            "image": image_source,
            "title": title,
            "caption": caption,
            "summary": caption,
        },
    )


def _build_news_message(
    *,
    structured: dict[str, Any],
    payload: dict[str, Any],
    assistant_text: str,
    sources: object,
    language: str,
) -> ChatMessage | None:
    _ = payload, assistant_text, language
    items: list[dict[str, Any]] = []

    briefing = structured.get("briefing")
    if isinstance(briefing, dict):
        items.extend(_news_items_from_list(briefing.get("top_items")))
        items.extend(_news_items_from_list(briefing.get("project_related")))
    if not items:
        items.extend(_news_items_from_list(structured.get("project_related")))
    if not items:
        items.extend(_news_items_from_list(structured.get("observations")))
    if not items:
        article = structured.get("article")
        if isinstance(article, dict):
            analysis = structured.get("analysis") if isinstance(structured.get("analysis"), dict) else {}
            title = _clean_text(article.get("title"))
            url = _clean_text(article.get("url"))
            summary = _clean_text(analysis.get("short_comment")) or _clean_text(article.get("summary"))
            tags = list(article.get("tags", []) or [])
            if title and url:
                items.append({"title": title, "summary": summary, "tags": tags, "url": url})
    if not items and isinstance(sources, list) and len(sources) > 1:
        items.extend(_news_items_from_list(sources))
    items = [item for item in items if item.get("title") and item.get("url")]
    if not items:
        return None
    return ChatMessage.create(role="assistant", message_type="news", payload={"items": items[:5]})


def _build_link_message(
    *,
    structured: dict[str, Any],
    payload: dict[str, Any],
    assistant_text: str,
    sources: object,
    language: str,
) -> ChatMessage | None:
    _ = payload, language
    target_url = (
        _clean_text(structured.get("url"))
        or _clean_text(structured.get("download_url"))
        or _clean_text(structured.get("link_url"))
    )
    title = _clean_text(structured.get("title")) or _clean_text(structured.get("label"))
    if not target_url and isinstance(sources, list) and len(sources) == 1 and isinstance(sources[0], dict):
        source = sources[0]
        target_url = _clean_text(source.get("url"))
        title = title or _clean_text(source.get("title"))
    if not target_url:
        return None
    title = title or _first_sentence(assistant_text) or target_url
    return ChatMessage.create(
        role="assistant",
        message_type="link",
        payload={
            "title": title,
            "url": target_url,
        },
    )


def _build_suggestion_message(
    *,
    structured: dict[str, Any],
    payload: dict[str, Any],
    assistant_text: str,
    sources: object,
    language: str,
) -> ChatMessage | None:
    _ = payload, sources, language
    recommendation = _clean_text(structured.get("recommendation"))
    summary = _clean_text(structured.get("summary"))
    if not recommendation:
        return None
    if assistant_text and len(assistant_text.strip()) > 220:
        return None
    body = recommendation
    if summary and summary != recommendation:
        body = f"{summary}\n{recommendation}".strip()
    return ChatMessage.create(
        role="assistant",
        message_type="suggestion",
        payload={
            "body": body,
            "rich_text": False,
        },
    )


def _news_items_from_list(raw_items: object) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not isinstance(raw_items, list):
        return items
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        title = _clean_text(raw.get("title"))
        url = _clean_text(raw.get("url"))
        if not title or not url:
            continue
        summary = _clean_text(raw.get("summary")) or _clean_text(raw.get("short_comment"))
        if not summary:
            reasons = raw.get("reasons")
            if isinstance(reasons, list) and reasons:
                summary = _clean_text(reasons[0])
        tags = raw.get("tags")
        if not isinstance(tags, list):
            tags = []
        items.append({"title": title, "summary": summary, "tags": [str(tag) for tag in tags[:3]], "url": url})
    return items


def _build_static_map_url(lat: float | None, lon: float | None) -> str:
    if lat is None or lon is None:
        return ""
    return (
        "https://staticmap.openstreetmap.de/staticmap.php"
        f"?center={lat:.6f},{lon:.6f}&zoom=13&size=640x360&markers={lat:.6f},{lon:.6f},red-pushpin"
    )


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _float_or_none(value: object) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _display_number(value: object) -> str:
    number = _float_or_none(value)
    if number is None:
        return "--"
    return str(int(round(number)))


def _number_list(value: object) -> list[float]:
    if not isinstance(value, list):
        return []
    result: list[float] = []
    for item in value:
        number = _float_or_none(item)
        if number is not None:
            result.append(number)
    return result


def _first_sentence(text: str) -> str:
    compact = " ".join((text or "").split())
    if not compact:
        return ""
    for separator in ("。", ".", "!", "！", "?", "？", "\n"):
        if separator in compact:
            return compact.split(separator, 1)[0].strip()
    return compact[:72].strip()


def _weather_icon_type(condition: str) -> str:
    lowered = condition.lower()
    if any(token in lowered for token in ("rain", "shower", "storm", "雷", "雨")):
        return "rain"
    if any(token in lowered for token in ("cloud", "overcast", "阴", "云")):
        return "cloud"
    if any(token in lowered for token in ("snow", "雪")):
        return "snow"
    if any(token in lowered for token in ("fog", "mist", "雾")):
        return "fog"
    return "sun"
