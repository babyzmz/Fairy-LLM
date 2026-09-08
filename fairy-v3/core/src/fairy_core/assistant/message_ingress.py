import json
from collections.abc import Callable
from hashlib import sha256
from threading import RLock

from fairy_core.application.core import CoreApplication
from fairy_core.assistant.models import AssistantTurn
from fairy_core.assistant.turn_scheduler import AssistantTurnScheduler
from fairy_core.assistant.turn_selection import resolve_turn_model_source
from fairy_core.commanding import EventVisibility
from fairy_core.contracts.common import ExecutionTarget
from fairy_core.contracts.message_ingress import AssistantMessageSubmitInput
from fairy_core.contracts.models import AssistantTurnCreateInput, TaskCreate
from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.domain.models import OperationMode, WorkspaceType
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import ProviderRegistry
from fairy_core.providers.ports import ProviderUnavailableError


class AssistantMessageIngress:
    """Core-owned text submission; execution remains in the existing Workflow Kernel."""

    def __init__(
        self, *, application: CoreApplication,
        scheduler: AssistantTurnScheduler, providers: ProviderRegistry,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        turn_factory: Callable[[AssistantTurnCreateInput], AssistantTurn],
    ) -> None:
        self._application = application
        self._scheduler = scheduler
        self._providers = providers
        self._units = unit_of_work_factory
        self._turn_factory = turn_factory
        self._lock = RLock()

    def submit(self, request: AssistantMessageSubmitInput) -> AssistantTurn:
        with self._lock:
            return self._submit(request)

    def _submit(self, request: AssistantMessageSubmitInput) -> AssistantTurn:
        key = sha256(request.idempotency_key.strip().encode("utf-8")).hexdigest()
        payload = request.model_dump(mode="json", exclude={"idempotency_key"})
        payload["content"] = request.content.strip()
        digest = sha256(json.dumps(
            payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
        ).encode("ascii")).hexdigest()
        task_key, turn_key = f"message-task:{key}", f"message-turn:{key}"
        with self._units() as unit:
            previous_digest = unit.assistant.message_submission_digest(key)
            if previous_digest is not None and previous_digest != digest:
                raise IdempotencyConflictError("Message idempotency key has a different request")
            existing = unit.assistant.find_turn_by_idempotency_key(turn_key)
            if existing is not None:
                task = unit.state.get_task(existing.task_id)
                selected = request.model_selection
                previous = existing.model_selection
                same_model = (
                    existing.profile_id == request.profile_id and previous is None
                    if selected is None else previous is not None and (
                        previous.mode, previous.model_id, previous.revision
                    ) == (selected.mode, selected.model_id, selected.revision)
                )
                if (
                    task is None or task.conversation_id != request.conversation_id
                    or task.user_request != request.content.strip() or not same_model
                ):
                    raise IdempotencyConflictError(
                        "Message idempotency key has a different request"
                    )
            else:
                conversation = unit.state.get_conversation(request.conversation_id)
                if conversation is None:
                    raise KeyError("Message conversation is unavailable")
                if (
                    request.source == "pet"
                    and conversation.workspace_type is not WorkspaceType.CHAT_SCRATCH
                ):
                    raise InvalidTransitionError(
                        "Pet messages require an explicitly bound scratch chat"
                    )
                if unit.assistant.nonterminal_turn_for_conversation(request.conversation_id):
                    raise InvalidTransitionError("Conversation has an active or stopping task")
        if existing is not None:
            # A transport replay acknowledges the original submission. It is not a
            # user request to resume a paused/waiting workflow or retry a failure.
            return existing

        selected = request.model_selection
        profile_id, _ = resolve_turn_model_source(
            requested_mode=selected.mode if selected else None,
            requested_model_id=selected.model_id if selected else None,
            requested_revision=selected.revision if selected else None,
            legacy_profile_id=request.profile_id, has_attachments=False,
            unit_of_work_factory=self._units, providers=self._providers,
        )
        if not self._providers.profile(profile_id).enabled:
            raise ProviderUnavailableError("Selected provider profile is disabled")
        with self._units() as unit:
            unit.assistant.reserve_message_submission(
                key_digest=key, request_digest=digest, conversation_id=request.conversation_id,
            )
            unit.commit()
        context = self._application.create_task(TaskCreate(
            conversation_id=request.conversation_id, user_request=request.content,
            operation_mode=OperationMode.ANSWER, execution_target=ExecutionTarget.LOCAL,
            idempotency_key=task_key,
        ))
        turn = self._turn_factory(AssistantTurnCreateInput(
            task_id=context.task.id, profile_id=request.profile_id,
            model_selection=selected, idempotency_key=turn_key,
        ))
        with self._units() as unit:
            unit.commands.append_domain_event(
                event_type="assistant.message.submitted", visibility=EventVisibility.USER,
                message="Message accepted by Core", actor="user",
                conversation_id=turn.conversation_id, task_id=turn.task_id,
                payload={"turn_id": str(turn.id), "source": request.source},
            )
            unit.commit()
        return self._scheduler.start(turn.id)
