import json
from collections.abc import Callable, Sequence
from hashlib import sha256
from threading import RLock
from typing import Any

from fairy_core.application.core import CoreApplication
from fairy_core.assistant.models import AssistantTurnStatus
from fairy_core.contracts.domain_commands import AssistantCommandInput
from fairy_core.contracts.models import AssistantTurnCancelInput
from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory


class AssistantDomainCommands:
    """Explicit local commands; UI actions never grant execution permissions."""

    def __init__(
        self, *, application: CoreApplication, units: CoreUnitOfWorkFactory,
        commands: Callable[[], Sequence[dict[str, Any]]],
        cancel: Callable[[AssistantTurnCancelInput], Any],
    ) -> None:
        self._application = application
        self._units = units
        self._commands = commands
        self._cancel = cancel
        self._lock = RLock()
        self._stop_lock = RLock()

    def dispatch(self, request: AssistantCommandInput) -> dict[str, Any]:
        parts = request.text.strip().split()
        if not parts or not parts[0].startswith("/"):
            raise ValueError("Expected an explicit slash command")
        name = parts[0][1:]
        if name == "stop":
            if len(parts) != 1:
                raise ValueError("Command does not accept arguments")
            key, digest = self._identity(request)
            # Stopping an already bound Turn is not permission to start model work.
            # Never refresh extensions or wait for new-chat filesystem preparation.
            with self._stop_lock:
                return {"command": name, "turn": self._stop(request, key, digest)}
        commands = self._commands()
        definition = next((item for item in commands if item["name"] == name), None)
        if definition is None:
            raise ValueError("Unknown slash command")
        if not definition["available"]:
            raise InvalidTransitionError("Command is unavailable under current device policy")
        if name == "permission":
            if len(parts) != 2 or parts[1] not in {"observe", "standard", "autonomous"}:
                raise ValueError("Expected /permission <observe|standard|autonomous>")
            return {"command": name, "ui_action": {
                "kind": "request_permission", "profile": parts[1],
            }}
        if len(parts) != 1:
            raise ValueError("Command does not accept arguments")
        if name == "project":
            return {"command": name, "ui_action": {"kind": "show_project"}}
        if name == "help":
            return {"command": name, "notice": " | ".join(
                f"/{item['name']}" + (
                    f" {item['argument_hint']}" if item.get("argument_hint") else ""
                ) for item in commands if item["available"]
            )}
        if name not in {"new", "clear"}:
            raise ValueError("Slash command has no domain handler")
        key, digest = self._identity(request)
        with self._lock:
            return {"command": name, "conversation": self._new(request, key, digest)}

    @staticmethod
    def _identity(request: AssistantCommandInput) -> tuple[str, str]:
        key = sha256(
            ("assistant.commands.dispatch\0" + request.idempotency_key.strip()).encode(),
        ).hexdigest()
        payload = request.model_dump(mode="json", exclude={"idempotency_key"})
        payload["text"] = request.text.strip()
        digest = sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        return key, digest

    def _existing(self, unit, key: str, digest: str):
        existing = unit.assistant.message_submission_digest(key)
        if existing is not None and existing != digest:
            raise IdempotencyConflictError("Command idempotency key has a different request")
        return unit.assistant.message_submission_conversation(key) if existing else None

    @staticmethod
    def _conversation(unit, conversation_id):
        conversation = unit.state.get_conversation(conversation_id)
        if conversation is None or any((
            conversation.deleted_at, conversation.deleted_by_project_at, conversation.purged_at,
        )):
            raise InvalidTransitionError("Command conversation is unavailable")
        return conversation

    def _new(self, request, key, digest):
        created = None
        try:
            with self._units() as unit:
                existing = self._existing(unit, key, digest)
                if existing is not None:
                    return self._conversation(unit, existing)
                if (
                    request.turn_id is not None
                    or request.expected_cancellation_revision is not None
                ):
                    raise ValueError("New conversation commands cannot target a Turn")
                if request.conversation_id is not None:
                    self._conversation(unit, request.conversation_id)
                created = self._application.create_scratch_conversation_in_unit_of_work(unit)
                unit.assistant.reserve_message_submission(
                    key_digest=key, request_digest=digest, conversation_id=created.id,
                )
                unit.commit()
                return created
        except BaseException:
            if created is not None:
                self._application.purge_scratch_conversation(created)
            raise

    def _stop(self, request, key, digest):
        if (
            request.conversation_id is None or request.turn_id is None
            or request.expected_cancellation_revision is None
        ):
            raise ValueError("Stop requires conversation, Turn and cancellation revision")
        with self._units() as unit:
            self._conversation(unit, request.conversation_id)
            turn = unit.assistant.get_turn(request.turn_id)
            if turn is None or turn.conversation_id != request.conversation_id:
                raise InvalidTransitionError("Stop Turn belongs to a different conversation")
            previous = self._existing(unit, key, digest)
            if (
                previous is not None and turn.status is AssistantTurnStatus.CANCELLED
                and turn.cancellation_revision == request.expected_cancellation_revision + 1
            ):
                return turn
            unit.assistant.reserve_message_submission(
                key_digest=key, request_digest=digest, conversation_id=request.conversation_id,
            )
            unit.commit()
        return self._cancel(AssistantTurnCancelInput(
            turn_id=request.turn_id,
            expected_cancellation_revision=request.expected_cancellation_revision,
        ))
