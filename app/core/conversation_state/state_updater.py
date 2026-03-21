"""State Updater — extracts context from agent results and updates conversation state."""
from __future__ import annotations
import logging
import re
import time
from typing import Any
from app.core.conversation_state.state_models import ConversationState, IntentFrame

logger = logging.getLogger(__name__)


def _extract_entity_from_card(card_type: str, payload: dict) -> str | None:
    keys_by_type: dict[str, list[str]] = {
        "time_card":    ["city", "location"],
        "weather_card": ["city", "location"],
        "crypto_card":  ["symbol", "ticker"],
        "stock_card":   ["symbol", "ticker"],
        "fx_card":      ["pair", "from_currency"],
        "fuel_card":    ["location", "suburb"],
        "map_card":     ["name", "location"],
        "news_card":    ["topic", "query"],
        "web_research_card": ["entity", "query"],
        "docs_card":    ["entity", "query"],
    }
    for key in keys_by_type.get(card_type, ["entity", "name", "query"]):
        val = payload.get(key)
        if val and isinstance(val, str):
            return val.strip() or None
    return None


def _extract_location(card_type: str, payload: dict) -> str | None:
    if card_type not in {"time_card", "weather_card", "fuel_card", "map_card"}:
        return None
    for key in ("city", "location", "suburb", "name"):
        val = payload.get(key)
        if val and isinstance(val, str):
            return val.strip() or None
    return None


_LOCATION_SUFFIXES = re.compile(
    r"(\u5728\u54ea\u91cc|\u5730\u7406|\u5750\u6807|\u4eba\u53e3|\u9762\u79ef|"
    r"\u5929\u6c14|\u5730\u56fe|\u591a\u8fdc|\u73b0\u5728|\u51e0\u70b9|"
    r"\u4ef7\u683c|\u591a\u5c11|\u80a1\u4ef7|\u6c47\u7387|"
    r" population| map| coordinates| weather| time| location| price| rate)",
    re.I,
)
_CRYPTO_TOKENS = re.compile(r"\b(BTC|ETH|BNB|SOL|XRP|DOGE|ADA|USDT)\b")


def _fallback_entity_from_query(query: str) -> str | None:
    """Last-resort entity extraction from the resolved query text."""
    q = (query or "").strip()
    m = _CRYPTO_TOKENS.search(q)
    if m:
        return m.group(1)
    remainder = _LOCATION_SUFFIXES.sub("", q).strip()
    if re.search(r"[\u4e00-\u9fff]", remainder):
        m2 = re.match(r"^([\u4e00-\u9fff]{2,8})", remainder)
        if m2:
            return m2.group(1)
    words = remainder.split()
    caps = [w for w in words[:4] if w[:1].isupper()]
    if caps:
        return " ".join(caps[:2])
    return None


class StateUpdater:
    @staticmethod
    def update(
        state: ConversationState,
        *,
        query: str,
        resolved_query: str,
        agent_result: dict[str, Any],
        agent_name: str = "",
    ) -> None:
        if not agent_result.get("success", False):
            return
        try:
            StateUpdater._apply(state, query, resolved_query, agent_result, agent_name)
        except Exception as exc:
            logger.warning("state_updater_error error=%s", exc)

    @staticmethod
    def _apply(
        state: ConversationState,
        query: str,
        resolved_query: str,
        result: dict[str, Any],
        agent_name: str,
    ) -> None:
        now = time.time()
        card = result.get("card") or {}
        card_type = str(card.get("type") or result.get("card_type") or "").strip()
        payload: dict = dict(card) if isinstance(card, dict) else {}
        intent = (
            result.get("intent")
            or result.get("subtype")
            or card_type.replace("_card", "")
            or ""
        )
        # Primary entity extraction
        entity = result.get("entity") or _extract_entity_from_card(card_type, payload) or None
        # Fallback: extract from resolved_query if primary fails
        if not entity and resolved_query:
            entity = _fallback_entity_from_query(resolved_query)
            if entity:
                logger.debug("state_updater fallback_entity=%s from_query=%s",
                             entity, resolved_query[:40])
        location = (
            result.get("location")
            or _extract_location(card_type, payload)
            or (entity if intent in {"time", "weather", "fuel", "map", "location"} else None)
        )
        domain = (
            result.get("domain")
            or ("realtime_lookup" if "realtime" in agent_name.lower() else "")
            or ""
        )
        state.turn_index += 1
        state.last_user_message = query
        state.last_resolved_query = resolved_query
        state.last_update_ts = now
        if entity:
            state.last_entity = entity
        if location:
            state.last_location = location
        if intent:
            state.last_intent = intent
        if domain:
            state.last_domain = domain
        if agent_name:
            state.last_agent = agent_name
        if card_type:
            state.last_card_type = card_type
        if payload:
            state.last_card_payload = payload
        state.push_frame(IntentFrame(
            intent=intent or "unknown",
            entity=entity,
            slots={"location": location, "card_type": card_type, "domain": domain},
            created_at=now,
            confidence=float(result.get("confidence", 1.0)),
        ))
        logger.debug(
            "state_updated session=%s turn=%d entity=%s intent=%s",
            state.session_id, state.turn_index, entity, intent,
        )
