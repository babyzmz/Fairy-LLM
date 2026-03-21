"""Conversation State Store — in-memory session registry with expiry.

Working memory only. No persistence.
"""

from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Iterator

from app.core.conversation_state.state_models import ConversationState

logger = logging.getLogger(__name__)

# Sessions expire after 10 minutes of inactivity
_EXPIRY_SECONDS = 600


class ConversationStateStore:
    """Thread-safe in-memory store for ConversationState objects.

    Sessions are automatically expired after ``_EXPIRY_SECONDS`` of
    inactivity. Expiry is checked lazily on every access.
    """

    def __init__(self, expiry_seconds: float = _EXPIRY_SECONDS) -> None:
        self._states: dict[str, ConversationState] = {}
        self._lock = Lock()
        self._expiry = expiry_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, session_id: str) -> ConversationState | None:
        """Return state if it exists and has not expired."""
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return None
            if self._is_expired(state):
                del self._states[session_id]
                logger.debug("conversation_state_expired session=%s", session_id)
                return None
            return state

    def get_or_create(self, session_id: str) -> ConversationState:
        """Return existing (non-expired) state or create a fresh one."""
        with self._lock:
            state = self._states.get(session_id)
            if state is not None and not self._is_expired(state):
                return state
            state = ConversationState(
                session_id=session_id,
                last_update_ts=time.time(),
            )
            self._states[session_id] = state
            logger.debug("conversation_state_created session=%s", session_id)
            return state

    def update(self, session_id: str, state: ConversationState) -> None:
        """Persist an updated state object."""
        with self._lock:
            state.last_update_ts = time.time()
            self._states[session_id] = state

    def clear(self, session_id: str) -> None:
        """Remove a session entirely."""
        with self._lock:
            self._states.pop(session_id, None)

    def purge_expired(self) -> int:
        """Remove all expired sessions. Returns count removed."""
        now = time.time()
        with self._lock:
            expired = [
                sid for sid, s in self._states.items()
                if (now - s.last_update_ts) > self._expiry
            ]
            for sid in expired:
                del self._states[sid]
        if expired:
            logger.debug("conversation_state_purged count=%d", len(expired))
        return len(expired)

    def session_count(self) -> int:
        with self._lock:
            return len(self._states)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _is_expired(self, state: ConversationState) -> bool:
        return (time.time() - state.last_update_ts) > self._expiry


# Module-level singleton
_store: ConversationStateStore | None = None


def get_store() -> ConversationStateStore:
    """Return the shared default store (lazy singleton)."""
    global _store
    if _store is None:
        _store = ConversationStateStore()
    return _store
