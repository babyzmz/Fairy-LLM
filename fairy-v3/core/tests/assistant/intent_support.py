from uuid import UUID

from fairy_core.assistant.interpretation import (
    AssistantRequestInterpretationRevision,
    InterpretedObjective,
    RequestAction,
)
from fairy_core.assistant.models import MessageRole


def bind_test_intent(service, turn, *, action: RequestAction, targets: tuple[str, ...]):
    """Supply the interpreted-request prerequisite of downstream executor tests.

    Classifier behavior is exercised separately; Scope, Policy, Approval, execution
    and persistence remain real here. Do not use this in intent/classifier tests.
    """
    turn_id = UUID(str(turn["id"]))
    with service._unit_of_work_factory() as unit:
        source = unit.assistant.message_for_turn(turn_id, MessageRole.USER)
        assert source is not None
        revision = AssistantRequestInterpretationRevision.create(
            turn_id=turn_id, revision=1,
            source_message_id=source.id, source_message=source.content,
            normalized_goal=source.content, action=action,
            objectives=(InterpretedObjective(source.content, action),),
            targets=targets, public_summary="The requested action and target are resolved",
        )
        unit.assistant.append_interpretation(revision, expected_revision=None)
        unit.commit()
