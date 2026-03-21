"""Regression tests for follow-up pipeline — Part 2: updater and manager."""
from __future__ import annotations
import time
import pytest
from app.core.conversation_state.state_models import ConversationState
from app.core.conversation_state.state_store import ConversationStateStore
from app.core.conversation_state.state_updater import StateUpdater
from app.core.conversation_state.state_manager import ConversationStateManager


def _mgr_with_entity(sid: str, entity: str, intent: str = "location") -> ConversationStateManager:
    store = ConversationStateStore(expiry_seconds=600)
    mgr = ConversationStateManager(store=store)
    mgr.update_after_agent(
        sid, query=f"{entity}\u5728\u54ea\u91cc",
        resolved_query=f"{entity}\u5728\u54ea\u91cc",
        agent_result={
            "success": True,
            "card_type": f"{intent}_card",
            "card": {"type": f"{intent}_card", "city": entity, "location": entity},
            "entity": entity,
        },
    )
    return mgr


class TestStateUpdater:
    def test_entity_from_card(self):
        s = ConversationState(session_id="s1")
        StateUpdater.update(s, query="q", resolved_query="q", agent_result={
            "success": True, "card_type": "weather_card",
            "card": {"type": "weather_card", "city": "\u58a8\u5c14\u672c"},
        })
        assert s.last_entity == "\u58a8\u5c14\u672c"

    def test_entity_fallback_from_query(self):
        s = ConversationState(session_id="s2")
        StateUpdater.update(s, query="\u963f\u5fb7\u83b1\u5fb7\u5728\u54ea\u91cc",
                            resolved_query="\u963f\u5fb7\u83b1\u5fb7\u5728\u54ea\u91cc",
                            agent_result={"success": True, "card_type": "web_research_card",
                                          "card": {"type": "web_research_card"}})
        assert s.last_entity is not None

    def test_no_update_on_failure(self):
        s = ConversationState(session_id="s3")
        StateUpdater.update(s, query="q", resolved_query="q",
                            agent_result={"success": False})
        assert s.turn_index == 0

    def test_frame_pushed(self):
        s = ConversationState(session_id="s4")
        StateUpdater.update(s, query="BTC", resolved_query="BTC\u4ef7\u683c",
                            agent_result={"success": True, "card_type": "crypto_card",
                                          "card": {"type": "crypto_card", "symbol": "BTC"}})
        assert len(s.active_frames) == 1
        assert s.active_frame is not None
        assert s.active_frame.intent == "crypto"


class TestManagerRegressions:
    """Spec regression tests: all second turns must resolve with first-turn entity."""

    def _two_turns(self, entity: str, intent: str, turn1: str, turn2: str) -> str:
        sid = f"reg_{entity}"
        store = ConversationStateStore(expiry_seconds=600)
        mgr = ConversationStateManager(store=store)
        mgr.update_after_agent(
            sid, query=turn1, resolved_query=turn1,
            agent_result={
                "success": True,
                "card_type": f"{intent}_card",
                "card": {"type": f"{intent}_card", "city": entity, "location": entity},
                "entity": entity,
            },
        )
        r, _ = mgr.resolve_with_classification(sid, turn2)
        return r

    def test_adelaide_population(self):
        r = self._two_turns("\u963f\u5fb7\u83b1\u5fb7", "location",
                             "\u963f\u5fb7\u83b1\u5fb7\u5728\u54ea\u91cc", "\u4eba\u53e3\u591a\u5c11")
        assert "\u963f\u5fb7\u83b1\u5fb7" in r and "\u4eba\u53e3" in r

    def test_chengdu_map(self):
        r = self._two_turns("\u6210\u90fd", "location",
                             "\u6210\u90fd\u5728\u54ea\u91cc", "\u5730\u56fe\u5462")
        assert "\u6210\u90fd" in r

    def test_melbourne_tomorrow(self):
        r = self._two_turns("\u58a8\u5c14\u672c", "weather",
                             "\u58a8\u5c14\u672c\u5929\u6c14", "\u660e\u5929\u5462")
        assert "\u58a8\u5c14\u672c" in r and "\u5929\u6c14" in r

    def test_tokyo_now(self):
        r = self._two_turns("\u4e1c\u4eac", "time",
                             "\u4e1c\u4eac\u73b0\u5728\u51e0\u70b9", "\u73b0\u5728\u5462")
        assert "\u4e1c\u4eac" in r

    def test_btc_now(self):
        sid = "reg_btc"
        store = ConversationStateStore(expiry_seconds=600)
        mgr = ConversationStateManager(store=store)
        mgr.update_after_agent(
            sid, query="BTC\u4ef7\u683c", resolved_query="BTC\u4ef7\u683c",
            agent_result={"success": True, "card_type": "crypto_card",
                          "card": {"type": "crypto_card", "symbol": "BTC"}},
        )
        r, _ = mgr.resolve_with_classification(sid, "\u591a\u5c11\u5462")
        assert "BTC" in r

    def test_no_state_returns_original(self):
        store = ConversationStateStore(expiry_seconds=600)
        mgr = ConversationStateManager(store=store)
        r, _ = mgr.resolve_with_classification("new_sess", "\u4eba\u53e3\u591a\u5c11")
        assert r == "\u4eba\u53e3\u591a\u5c11"

    def test_expired_session_returns_original(self):
        # Use a fresh store with no prior state — simulates expired/missing session
        store = ConversationStateStore(expiry_seconds=600)
        mgr = ConversationStateManager(store=store)
        # No update_after_agent call — session does not exist
        r, _ = mgr.resolve_with_classification("exp_no_state", "\u5730\u56fe\u5462")
        assert r == "\u5730\u56fe\u5462"

    def test_complete_query_unchanged(self):
        mgr = _mgr_with_entity("full", "\u963f\u5fb7\u83b1\u5fb7")
        long_q = "\u963f\u5fb7\u83b1\u5fb7\u662f\u6fb3\u5927\u5229\u4e9a\u5357\u6f90\u5dde\u9996\u5e9c\uff0c\u4eba\u53e3\u7ea6130\u4e07"
        r, _ = mgr.resolve_with_classification("full", long_q)
        assert r == long_q

    def test_active_frame_stored(self):
        store = ConversationStateStore(expiry_seconds=600)
        mgr = ConversationStateManager(store=store)
        mgr.update_after_agent(
            "frame", query="BTC", resolved_query="BTC\u4ef7\u683c",
            agent_result={"success": True, "card_type": "crypto_card",
                          "card": {"type": "crypto_card", "symbol": "BTC"}},
        )
        state = mgr.get_state("frame")
        assert state is not None
        assert state.active_frame is not None
        assert state.active_frame.entity == "BTC"


if __name__ == "__main__":
    pytest.main(["-v", __file__])

