from uuid import UUID

from fairy_core.assistant.models import MessageRole, MessageVisibility
from fairy_core.contracts.models import ConversationIdInput
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.models import WorkspaceType
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory


class AssistantPresentationService:
    def __init__(self, units: CoreUnitOfWorkFactory) -> None:
        self._units = units

    def get(self, request: ConversationIdInput) -> dict:
        with self._units() as unit:
            conversation = unit.state.get_conversation(request.conversation_id)
            if (
                conversation is None
                or conversation.workspace_type is not WorkspaceType.CHAT_SCRATCH
                or conversation.deleted_at is not None
                or conversation.deleted_by_project_at is not None
                or conversation.purged_at is not None
            ):
                raise InvalidTransitionError("Pet conversation is unavailable")
            turn = unit.assistant.latest_turn_presentation(request.conversation_id)
            reply = None
            if turn is not None:
                message = unit.assistant.message_for_turn(
                    UUID(str(turn["id"])), MessageRole.ASSISTANT,
                )
                if (
                    message is not None and message.visibility is MessageVisibility.USER
                    and message.content.strip()
                ):
                    reply = {"id": message.id, "text": message.content[:1200]}
            return {"conversation_id": request.conversation_id, "turn": turn, "reply": reply}
