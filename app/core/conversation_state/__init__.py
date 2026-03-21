"""app.core.conversation_state — working-memory conversation state engine."""
from app.core.conversation_state.state_models import ConversationState, IntentFrame
from app.core.conversation_state.state_store import ConversationStateStore, get_store
from app.core.conversation_state.followup_classifier import (
    FollowUpClassifier, FollowUpClassification, FollowUpType,
)
from app.core.conversation_state.reference_resolver import ReferenceResolver
from app.core.conversation_state.state_updater import StateUpdater
from app.core.conversation_state.state_manager import ConversationStateManager, get_manager

__all__ = [
    "ConversationState",
    "IntentFrame",
    "ConversationStateStore",
    "get_store",
    "FollowUpClassifier",
    "FollowUpClassification",
    "FollowUpType",
    "ReferenceResolver",
    "StateUpdater",
    "ConversationStateManager",
    "get_manager",
]
