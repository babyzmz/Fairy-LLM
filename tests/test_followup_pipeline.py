"""Regression tests for follow-up pipeline — Part 1: classifier and resolver."""
from __future__ import annotations
import time
import pytest
from app.core.conversation_state.state_models import ConversationState
from app.core.conversation_state.state_store import ConversationStateStore
from app.core.conversation_state.followup_classifier import FollowUpClassifier, FollowUpType
from app.core.conversation_state.reference_resolver import ReferenceResolver
from app.core.conversation_state.state_updater import StateUpdater, _fallback_entity_from_query
from app.core.conversation_state.state_manager import ConversationStateManager


def _state(entity: str, intent: str = "location", card_type: str = "map_card") -> ConversationState:
    s = ConversationState(session_id="test")
    s.last_entity = entity
    s.last_intent = intent
    s.last_card_type = card_type
    s.last_update_ts = time.time()
    return s


class TestFollowUpClassifier:
    def test_population_elliptical(self):
        c = FollowUpClassifier.classify("\u4eba\u53e3\u591a\u5c11", has_active_entity=True)
        assert c.follow_up_type == FollowUpType.ELLIPTICAL_FOLLOWUP
        assert c.confidence >= 0.9

    def test_quantity_no_entity_ambiguous(self):
        c = FollowUpClassifier.classify("\u4eba\u53e3\u591a\u5c11", has_active_entity=False)
        assert c.follow_up_type == FollowUpType.AMBIGUOUS_FOLLOWUP

    def test_map_followup(self):
        assert FollowUpClassifier.classify("\u5730\u56fe\u5462", has_active_entity=True).is_followup

    def test_coordinates_followup(self):
        assert FollowUpClassifier.classify("\u5750\u6807\u5462", has_active_entity=True).is_followup

    def test_now_followup(self):
        c = FollowUpClassifier.classify("\u73b0\u5728\u5462", has_active_entity=True)
        assert c.follow_up_type == FollowUpType.ELLIPTICAL_FOLLOWUP

    def test_tomorrow_followup(self):
        assert FollowUpClassifier.classify("\u660e\u5929\u5462", has_active_entity=True).is_followup

    def test_how_far_elliptical(self):
        c = FollowUpClassifier.classify("\u591a\u8fdc", has_active_entity=True)
        assert c.follow_up_type == FollowUpType.ELLIPTICAL_FOLLOWUP

    def test_how_big_elliptical(self):
        c = FollowUpClassifier.classify("\u591a\u5927", has_active_entity=True)
        assert c.follow_up_type == FollowUpType.ELLIPTICAL_FOLLOWUP

    def test_complete_long_query(self):
        c = FollowUpClassifier.classify(
            "\u963f\u5fb7\u83b1\u5fb7\u662f\u6fb3\u5927\u5229\u4e9a\u5357\u6f90\u5dde\u7684\u9996\u5e9c",
            has_active_entity=True)
        assert c.follow_up_type == FollowUpType.COMPLETE_QUERY

    def test_new_topic_detected(self):
        c = FollowUpClassifier.classify("\u53e6\u5916\u67e5\u4e00\u4e0b\u5317\u4eac", has_active_entity=True)
        assert c.follow_up_type == FollowUpType.NEW_TOPIC

    def test_english_how_far(self):
        c = FollowUpClassifier.classify("how far", has_active_entity=True)
        assert c.follow_up_type == FollowUpType.ELLIPTICAL_FOLLOWUP

    def test_english_how_big(self):
        c = FollowUpClassifier.classify("how big", has_active_entity=True)
        assert c.follow_up_type == FollowUpType.ELLIPTICAL_FOLLOWUP


class TestReferenceResolver:
    def test_population_expands(self):
        r = ReferenceResolver.resolve("\u4eba\u53e3\u591a\u5c11", _state("\u963f\u5fb7\u83b1\u5fb7"))
        assert "\u963f\u5fb7\u83b1\u5fb7" in r and "\u4eba\u53e3" in r

    def test_map_expands(self):
        r = ReferenceResolver.resolve("\u5730\u56fe\u5462", _state("\u6210\u90fd"))
        assert "\u6210\u90fd" in r and "\u5730\u56fe" in r

    def test_coordinates_expands(self):
        r = ReferenceResolver.resolve("\u5750\u6807\u5462", _state("\u4e1c\u4eac"))
        assert "\u4e1c\u4eac" in r

    def test_tomorrow_weather_expands(self):
        r = ReferenceResolver.resolve("\u660e\u5929\u5462",
                                       _state("\u58a8\u5c14\u672c", "weather", "weather_card"))
        assert "\u58a8\u5c14\u672c" in r and "\u5929\u6c14" in r

    def test_now_expands(self):
        r = ReferenceResolver.resolve("\u73b0\u5728\u5462",
                                       _state("\u4e1c\u4eac", "time", "time_card"))
        assert "\u4e1c\u4eac" in r

    def test_how_far_expands(self):
        r = ReferenceResolver.resolve("\u591a\u8fdc", _state("\u963f\u5fb7\u83b1\u5fb7"))
        assert "\u963f\u5fb7\u83b1\u5fb7" in r

    def test_no_entity_returns_original(self):
        s = ConversationState(session_id="empty")
        assert ReferenceResolver.resolve("\u4eba\u53e3\u591a\u5c11", s) == "\u4eba\u53e3\u591a\u5c11"

    def test_long_query_unchanged(self):
        s = _state("\u963f\u5fb7\u83b1\u5fb7")
        q = "\u8fd9\u662f\u4e00\u4e2a\u975e\u5e38\u957f\u7684\u5b8c\u6574\u67e5\u8be2\u5173\u4e8e\u963f\u5fb7\u83b1\u5fb7\u7684\u5730\u7406\u4f4d\u7f6e"
        assert ReferenceResolver.resolve(q, s) == q

    def test_confidence_returned(self):
        s = _state("BTC", "crypto", "crypto_card")
        _, conf = ReferenceResolver.resolve_with_confidence("\u591a\u5c11\u5462", s)
        assert 0.0 <= conf <= 1.0


class TestFallbackEntityExtraction:
    def test_crypto(self):
        e = _fallback_entity_from_query("BTC\u73b0\u5728\u4ef7\u683c")
        assert e is not None and e.startswith("BTC")

    def test_chinese_entity(self):
        e = _fallback_entity_from_query("\u963f\u5fb7\u83b1\u5fb7\u4eba\u53e3\u591a\u5c11")
        assert e is not None and "\u963f\u5fb7" in e

    def test_empty_returns_none(self):
        assert _fallback_entity_from_query("") is None

