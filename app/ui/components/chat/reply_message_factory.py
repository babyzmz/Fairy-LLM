from __future__ import annotations

from typing import Any

from app.response.models import CardPayload, NormalizedAssistantResponse, SpeechPayload
from app.ui.components.chat.chat_message import ChatMessage


def build_assistant_chat_message(
    *,
    assistant_text: str,
    assistant_html: str = "",
    payload: dict[str, Any] | None = None,
    language: str = "zh_CN",
) -> ChatMessage:
    _ = payload, language
    return ChatMessage.create(
        role="assistant",
        message_type="text",
        payload={
            "speaker": "Fairy",
            "text": assistant_html or assistant_text,
            "plain_text": assistant_text,
            "rich_text": bool(assistant_html),
        },
    )


def build_chat_messages_from_response(
    response: NormalizedAssistantResponse | dict[str, Any],
    *,
    text_message_id: str | None = None,
) -> list[ChatMessage]:
    normalized = _coerce_response_contract(response)
    messages: list[ChatMessage] = []

    text_message: ChatMessage | None = None
    if normalized.text_reply.strip() or normalized.text_rich_html.strip():
        text_message = ChatMessage.create(
            role="assistant",
            message_type="text",
            payload={
                "speaker": "Fairy",
                "text": normalized.text_rich_html or normalized.text_reply,
                "plain_text": normalized.text_reply,
                "rich_text": bool(normalized.text_rich_html),
            },
            message_id=text_message_id,
        )

    card_messages: list[ChatMessage] = []
    for card in normalized.card_payloads:
        message_type = _card_type_to_message_type(card.card_type)
        if not message_type:
            continue
        payload = dict(card.payload)
        payload.setdefault("card_type", card.card_type)
        payload.setdefault("card_schema_type", card.type)
        payload.setdefault("card_version", card.version)
        payload.setdefault("card_layout", card.layout)
        payload.setdefault("layout_mode", card.layout)
        payload.setdefault("card_metadata", dict(card.metadata))
        card_messages.append(
            ChatMessage.create(
                role="assistant",
                message_type=message_type,
                payload=payload,
            )
        )

    if normalized.modality == "card_primary_text_summary":
        messages.extend(card_messages)
        if text_message is not None:
            messages.append(text_message)
        return messages

    if text_message is not None:
        messages.append(text_message)
    messages.extend(card_messages)
    return messages


def _coerce_response_contract(response: NormalizedAssistantResponse | dict[str, Any]) -> NormalizedAssistantResponse:
    if isinstance(response, NormalizedAssistantResponse):
        return response

    card_payloads: list[CardPayload] = []
    for item in list(response.get("cards", []) or []):
        if not isinstance(item, dict):
            continue
        card_payloads.append(
            CardPayload(
                type=str(item.get("type") or "generic_info").strip().lower(),
                version=str(item.get("version") or "1").strip() or "1",
                data=dict(item.get("data") or {}),
                layout=str(item.get("layout") or "single").strip().lower() or "single",
                metadata=dict(item.get("metadata") or {}),
            )
        )

    meta = dict(response.get("meta") or {})
    speech = dict(meta.get("speech") or {})
    modality = str(
        meta.get("modality")
        or ("card_primary_text_summary" if any(card.type == "location" for card in card_payloads) else "text_plus_card")
    ).strip()
    return NormalizedAssistantResponse(
        intent=str(meta.get("intent") or "").strip(),
        modality=modality,  # type: ignore[arg-type]
        text_reply=str(response.get("text") or "").strip(),
        request_id=str(response.get("request_id") or "").strip(),
        session_id=str(response.get("session_id") or "").strip(),
        speech_payload=SpeechPayload(
            mode=str(speech.get("mode") or "summary_first").strip() or "summary_first",  # type: ignore[arg-type]
            text=str(speech.get("text") or "").strip(),
            allow_streaming=bool(speech.get("allow_streaming")),
        ),
        card_payloads=card_payloads,
        meta=meta,
        errors=list(response.get("errors") or []),
    )


def _card_type_to_message_type(card_type: str) -> str:
    normalized = str(card_type or "").strip().lower()
    mapping = {
        "weather": "weather",
        "weather_card": "weather",
        "location": "map",
        "location_map_card": "map",
        "news_list": "news",
        "news_card": "news",
        "generic_info": "generic_info",
        "generic_info_card": "generic_info",
        "image_card": "image",
        "link_card": "link",
        "suggestion_card": "suggestion",
    }
    return mapping.get(normalized, "")
