"""Conversation State Manager — facade over store, resolver, classifier, and updater."""
from __future__ import annotations
import logging
from typing import Any

from app.core.conversation_state.followup_classifier import (
    FollowUpClassifier, FollowUpClassification, FollowUpType,
)
from app.core.conversation_state.reference_resolver import ReferenceResolver
from app.core.conversation_state.state_models import ConversationState
from app.core.conversation_state.state_store import ConversationStateStore, get_store
from app.core.conversation_state.state_updater import StateUpdater

logger = logging.getLogger(__name__)


class ConversationStateManager:
    """Facade: wires store + classifier + resolver + updater."""

    def __init__(self, store: ConversationStateStore | None = None) -> None:
        self._store = store or get_store()

    def resolve_query(self, session_id: str, message: str) -> str:
        """Expand follow-up reference query. Returns resolved string."""
        resolved, _ = self.resolve_with_classification(session_id, message)
        return resolved

    def resolve_with_classification(
        self, session_id: str, message: str,
    ) -> tuple[str, FollowUpClassification]:
        """Full resolution with classification metadata."""
        state = self._store.get(session_id)
        has_entity = state is not None and state.best_entity() is not None
        last_intent = state.last_intent if state else None

        classification = FollowUpClassifier.classify(
            message, has_active_entity=has_entity, last_intent=last_intent
        )

        if classification.follow_up_type == FollowUpType.NEW_TOPIC:
            logger.info("routing_query_source=RAW (new_topic) session=%s", session_id)
            return message, classification

        if not classification.is_followup:
            logger.info("routing_query_source=RAW (complete) session=%s", session_id)
            return message, classification

        if state is None:
            logger.info("followup_detected but no_state session=%s", session_id)
            return message, classification

        resolved, confidence = ReferenceResolver.resolve_with_confidence(message, state)

        if resolved == message:
            logger.info("reference_resolution_skipped session=%s raw=%r entity=%s",
                        session_id, message[:40], state.best_entity())
            return message, classification

        logger.info(
            "followup_detected session=%s type=%s confidence=%.2f "
            "raw=%r resolved=%r entity=%s intent=%s",
            session_id, classification.follow_up_type.value, confidence,
            message[:40], resolved[:60], state.best_entity(), state.last_intent,
        )
        logger.info("resolved_query_used_for_routing session=%s resolved=%r",
                    session_id, resolved[:60])
        return resolved, classification

    def update_after_agent(
        self, session_id: str, *, query: str, resolved_query: str,
        agent_result: dict[str, Any], agent_name: str = "",
    ) -> None:
        """Update session state after agent invocation."""
        state = self._store.get_or_create(session_id)
        StateUpdater.update(
            state, query=query, resolved_query=resolved_query,
            agent_result=agent_result, agent_name=agent_name,
        )
        self._store.update(session_id, state)
        logger.debug("state_updated session=%s entity=%s intent=%s",
                     session_id, state.last_entity, state.last_intent)

    def get_state(self, session_id: str) -> ConversationState | None:
        return self._store.get(session_id)

    def reset_if_expired(self, session_id: str) -> bool:
        return self._store.get(session_id) is None

    def clear_session(self, session_id: str) -> None:
        self._store.clear(session_id)

    def purge_expired(self) -> int:
        return self._store.purge_expired()

    def needs_clarification(
        self, classification: FollowUpClassification, resolved: str, original: str,
    ) -> bool:
        return classification.needs_clarification and resolved == original


_manager: ConversationStateManager | None = None


def get_manager() -> ConversationStateManager:
    global _manager
    if _manager is None:
        _manager = ConversationStateManager()
    return _manager
