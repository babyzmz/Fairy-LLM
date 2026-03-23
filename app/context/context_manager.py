from __future__ import annotations

from typing import Any

from app.agent.perception.followup_resolver import FollowUpContext, FollowUpResolver
from app.context.entity_focus_tracker import EntityFocusTracker
from app.context.session_context import SessionContext


class ContextManager:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionContext] = {}
        self._focus_tracker = EntityFocusTracker()
        self._followup_resolver = FollowUpResolver()

    def get_or_create(self, session_id: str) -> SessionContext:
        context = self._sessions.get(session_id)
        if context is None:
            context = SessionContext(session_id=session_id)
            self._sessions[session_id] = context
        return context

    def update_from_turn(self, session_id: str, frame, *, structured: dict[str, Any] | None = None) -> SessionContext:
        context = self.get_or_create(session_id)
        context.remember_turn(frame.normalized_text)
        self._focus_tracker.update_focus(context, frame, structured=structured)
        return context

    def mark_clarification(self, session_id: str, *, capability: str, message: str) -> SessionContext:
        context = self.get_or_create(session_id)
        context.last_clarification_target = str(capability or "").strip()
        context.last_clarification_message = str(message or "").strip()
        return context

    def clear_clarification(self, session_id: str) -> SessionContext:
        context = self.get_or_create(session_id)
        context.last_clarification_target = ""
        context.last_clarification_message = ""
        return context

    def resolve_followup(self, session_id: str, normalized_text: str, *, previous_structured: dict[str, Any] | None = None) -> FollowUpContext:
        context = self.get_or_create(session_id)
        followup = self._followup_resolver.resolve(
            normalized_text,
            previous_structured=previous_structured or context.last_structured,
        )
        if followup.target:
            if not followup.reason:
                followup.reason = "recent_context"
            followup.reused = True
            return followup
        return FollowUpContext()
