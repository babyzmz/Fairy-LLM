"""Unit tests for app.core.conversation_state.

Test 1: 成都在哪里 → 地理坐标呢  resolves without clarification
Test 2: BTC价格 → 现在多少  resolves to realtime price query
Test 3: After expiry, follow-up gets no resolution (returns original)
Test 4: Frame stack grows with conversation turns
"""

from __future__ import annotations

import time
import pytest

from app.core.conversation_state.state_models import ConversationState, IntentFrame
from app.core.conversation_state.state_store import ConversationStateStore
from app.core.conversation_state.reference_resolver import ReferenceResolver
from app.core.conversation_state.state_updater import StateUpdater
from app.core.conversation_state.state_manager import ConversationStateManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(session_id: str = "test", **kwargs) -> ConversationState:
    return ConversationState(session_id=session_id, last_update_ts=time.time(), **kwargs)


def _location_result(entity: str, location: str) -> dict:
    return {
        "success": True,
        "answer": f"{entity} is at ...",
        "card": {"type": "map_card", "name": entity, "location": location},
        "confidence": 0.95,
        "tool_lock": True,
    }


def _crypto_result(symbol: str, price: float) -> dict:
    return {
        "success": True,
        "answer": f"{symbol} = ${price}",
        "card": {"type": "crypto_card", "symbol": symbol, "price_usd": price},
        "confidence": 0.97,
        "tool_lock": True,
    }


# ---------------------------------------------------------------------------
# Test 1 — 成都在哪里 → 地理坐标呢
# ---------------------------------------------------------------------------

class TestLocationFollowUp:
    def test_coordinates_resolved(self):
        """After querying 成都 location, '地理坐标呢' should expand to include 成都."""
        store = ConversationStateStore()
        mgr = ConversationStateManager(store=store)
        sid = "session_t1"

        # Turn 1: user asks about 成都
        mgr.update_after_agent(
            sid,
            query="成都在哪里",
            resolved_query="成都在哪里",
            agent_result=_location_result("成都", "成都"),
            agent_name="MapAgent",
        )

        # Turn 2: follow-up — should resolve
        resolved = mgr.resolve_query(sid, "地理坐标呢")
        assert "成都" in resolved, f"Expected 成都 in resolved query, got: {resolved!r}"
        assert resolved != "地理坐标呢", "Query should have been expanded"

    def test_state_entity_set_after_update(self):
        store = ConversationStateStore()
        mgr = ConversationStateManager(store=store)
        sid = "session_t1b"
        mgr.update_after_agent(
            sid,
            query="成都在哪里",
            resolved_query="成都在哪里",
            agent_result=_location_result("成都", "成都"),
            agent_name="MapAgent",
        )
        state = mgr.get_state(sid)
        assert state is not None
        assert state.last_entity == "成都" or state.last_location == "成都"


# ---------------------------------------------------------------------------
# Test 2 — BTC价格 → 现在多少
# ---------------------------------------------------------------------------

class TestCryptoFollowUp:
    def test_btc_price_resolved(self):
        """After BTC price query, '现在多少' should expand to BTC price query."""
        store = ConversationStateStore()
        mgr = ConversationStateManager(store=store)
        sid = "session_t2"

        # Turn 1
        mgr.update_after_agent(
            sid,
            query="BTC价格",
            resolved_query="BTC价格",
            agent_result=_crypto_result("BTC", 67450.0),
            agent_name="RealtimeLookupAgent",
        )

        # Turn 2: follow-up
        resolved = mgr.resolve_query(sid, "现在多少")
        assert "BTC" in resolved, f"Expected BTC in resolved query, got: {resolved!r}"

    def test_domain_set_to_realtime(self):
        store = ConversationStateStore()
        mgr = ConversationStateManager(store=store)
        sid = "session_t2b"
        mgr.update_after_agent(
            sid,
            query="BTC价格",
            resolved_query="BTC价格",
            agent_result=_crypto_result("BTC", 67450.0),
            agent_name="RealtimeLookupAgent",
        )
        state = mgr.get_state(sid)
        assert state is not None
        assert state.last_domain == "realtime_lookup"
        assert state.last_intent == "crypto"


# ---------------------------------------------------------------------------
# Test 3 — After 10-min expiry, follow-up returns original message
# ---------------------------------------------------------------------------

class TestSessionExpiry:
    def test_expired_session_no_resolution(self):
        """Expired session returns original message unchanged."""
        store = ConversationStateStore(expiry_seconds=0.01)  # 10 ms
        mgr = ConversationStateManager(store=store)
        sid = "session_t3"

        mgr.update_after_agent(
            sid,
            query="成都在哪里",
            resolved_query="成都在哪里",
            agent_result=_location_result("成都", "成都"),
            agent_name="MapAgent",
        )

        # Wait for expiry
        time.sleep(0.05)

        resolved = mgr.resolve_query(sid, "地理坐标呢")
        assert resolved == "地理坐标呢", (
            f"Expired session should return original message, got: {resolved!r}"
        )

    def test_reset_if_expired_returns_true(self):
        store = ConversationStateStore(expiry_seconds=0.01)
        mgr = ConversationStateManager(store=store)
        sid = "session_t3b"
        mgr.update_after_agent(
            sid,
            query="BTC",
            resolved_query="BTC",
            agent_result=_crypto_result("BTC", 1.0),
            agent_name="RealtimeLookupAgent",
        )
        time.sleep(0.05)
        assert mgr.reset_if_expired(sid) is True

    def test_active_session_not_expired(self):
        store = ConversationStateStore(expiry_seconds=600)
        mgr = ConversationStateManager(store=store)
        sid = "session_t3c"
        mgr.update_after_agent(
            sid,
            query="BTC",
            resolved_query="BTC",
            agent_result=_crypto_result("BTC", 1.0),
            agent_name="RealtimeLookupAgent",
        )
        assert mgr.reset_if_expired(sid) is False


# ---------------------------------------------------------------------------
# Test 4 — Frame stack grows with conversation turns
# ---------------------------------------------------------------------------

class TestFrameStack:
    def test_frames_grow_per_turn(self):
        store = ConversationStateStore()
        mgr = ConversationStateManager(store=store)
        sid = "session_t4"

        queries = [
            ("BTC价格",  _crypto_result("BTC",  67000.0)),
            ("ETH价格",  _crypto_result("ETH",  3200.0)),
            ("NVDA股价", {"success": True, "answer": "875",
                          "card": {"type": "stock_card", "symbol": "NVDA"},
                          "confidence": 0.9, "tool_lock": True}),
        ]
        for i, (q, result) in enumerate(queries):
            mgr.update_after_agent(
                sid,
                query=q,
                resolved_query=q,
                agent_result=result,
                agent_name="RealtimeLookupAgent",
            )
            state = mgr.get_state(sid)
            assert state is not None
            assert len(state.active_frames) == i + 1, (
                f"Expected {i+1} frames after turn {i+1}, got {len(state.active_frames)}"
            )

    def test_frame_stack_capped_at_max(self):
        store = ConversationStateStore()
        mgr = ConversationStateManager(store=store)
        sid = "session_t4b"

        for i in range(15):  # exceed MAX_FRAMES=10
            mgr.update_after_agent(
                sid,
                query=f"query_{i}",
                resolved_query=f"query_{i}",
                agent_result=_crypto_result(f"T{i}", float(i)),
                agent_name="Agent",
            )

        state = mgr.get_state(sid)
        assert state is not None
        assert len(state.active_frames) <= 10

    def test_frame_fields(self):
        store = ConversationStateStore()
        mgr = ConversationStateManager(store=store)
        sid = "session_t4c"
        mgr.update_after_agent(
            sid,
            query="BTC价格",
            resolved_query="BTC价格",
            agent_result=_crypto_result("BTC", 67000.0),
            agent_name="RealtimeLookupAgent",
        )
        state = mgr.get_state(sid)
        assert state is not None
        assert len(state.active_frames) == 1
        frame = state.active_frames[0]
        assert frame.intent == "crypto"
        assert frame.entity == "BTC"
        assert frame.confidence == pytest.approx(0.97)
        assert frame.created_at > 0


# ---------------------------------------------------------------------------
# ReferenceResolver unit tests
# ---------------------------------------------------------------------------

class TestReferenceResolver:
    def test_no_state_returns_original(self):
        state = _make_state()
        result = ReferenceResolver.resolve("地理坐标呢", state)
        assert result == "地理坐标呢"

    def test_long_message_not_resolved(self):
        state = _make_state(last_entity="成都")
        long_msg = "我想了解一下成都这座城市的详细地理坐标信息以及周边环境"
        result = ReferenceResolver.resolve(long_msg, state)
        assert result == long_msg

    def test_no_marker_not_resolved(self):
        state = _make_state(last_entity="成都")
        result = ReferenceResolver.resolve("介绍一下成都", state)
        # No reference marker — should not expand
        assert result == "介绍一下成都"

    def test_crypto_now_price(self):
        state = _make_state(last_entity="BTC", last_intent="crypto")
        result = ReferenceResolver.resolve("现在多少", state)
        assert "BTC" in result

    def test_location_coordinates(self):
        state = _make_state(last_entity="成都", last_intent="location")
        result = ReferenceResolver.resolve("地理坐标呢", state)
        assert "成都" in result

    def test_english_marker(self):
        state = _make_state(last_entity="Melbourne", last_intent="weather")
        result = ReferenceResolver.resolve("weather now", state)
        assert "Melbourne" in result


# ---------------------------------------------------------------------------
# StateStore unit tests
# ---------------------------------------------------------------------------

class TestStateStore:
    def test_get_or_create(self):
        store = ConversationStateStore()
        s = store.get_or_create("s1")
        assert s.session_id == "s1"
        s2 = store.get_or_create("s1")
        assert s is s2

    def test_clear(self):
        store = ConversationStateStore()
        store.get_or_create("s2")
        store.clear("s2")
        assert store.get("s2") is None

    def test_purge_expired(self):
        store = ConversationStateStore(expiry_seconds=0.01)
        store.get_or_create("s3")
        time.sleep(0.05)
        removed = store.purge_expired()
        assert removed == 1
        assert store.session_count() == 0


if __name__ == "__main__":
    pytest.main(["-v", __file__])
