from dataclasses import replace
from uuid import UUID

import pytest

from fairy_core.assistant.execution_intent import ExecutionIntentSnapshot
from fairy_core.assistant.execution_intent_policy import (
    file_target_issue,
    intent_prohibitions,
    readonly_intent_issue,
)
from fairy_core.assistant.interpretation import (
    InterpretationConfidence,
    InterpretationDisposition,
    InterpretedObjective,
    RequestAction,
)
from fairy_core.assistant.system_intent import explicit_system_actions
from fairy_core.commanding.registry import build_default_registry


def _intent(action, *, targets=("current workspace",)):
    return ExecutionIntentSnapshot(
        turn_id=UUID(int=1),
        task_id=UUID(int=2),
        conversation_id=UUID(int=3),
        project_id=None,
        workspace_id=UUID(int=4),
        base_version_id=None,
        target_version_id=None,
        execution_target="local",
        scope_digest="a" * 64,
        interpretation_id=UUID(int=5),
        interpretation_revision=1,
        source_message_id=UUID(int=6),
        source_message_sha256="b" * 64,
        action=action,
        objectives=(InterpretedObjective("Requested action", action),),
        target_descriptions=targets,
        user_constraints=(),
        confidence=InterpretationConfidence.HIGH,
        disposition=InterpretationDisposition.READY,
    )


@pytest.mark.parametrize(
    "action,tool_name,allowed",
    [
        (RequestAction.ANSWER, "web.search", True),
        (RequestAction.REVIEW, "research.build", True),
        (RequestAction.REVIEW, "execution.plan", True),
        (RequestAction.REVIEW, "run.sandboxed", False),
        (RequestAction.CHANGE, "edit.propose_changeset", True),
        (RequestAction.CHANGE, "media.images.generate", False),
        (RequestAction.CHANGE, "system.notify", False),
        (RequestAction.GENERATE, "edit.propose_changeset", False),
        (RequestAction.GENERATE, "media.images.generate", True),
        (RequestAction.RUN, "memory.suggest", False),
        (RequestAction.BROWSE, "browser.fill", True),
        (RequestAction.MANAGE, "run.sandboxed", False),
    ],
)
def test_intent_actions_do_not_grant_unrelated_effects(action, tool_name, allowed):
    definition = build_default_registry().get(tool_name)
    assert definition is not None
    assert (readonly_intent_issue(_intent(action), definition) is None) is allowed


def test_direct_notification_does_not_authorize_other_management_actions():
    intent = _intent(RequestAction.MANAGE, targets=("system.notify",))
    registry = build_default_registry()
    assert readonly_intent_issue(intent, registry.get("system.notify")) is None
    assert readonly_intent_issue(intent, registry.get("memory.suggest")) is not None
    assert readonly_intent_issue(intent, registry.get("system.copy_text")) is not None


def test_extension_cannot_impersonate_internal_evidence_cache():
    original = build_default_registry().get("research.build")
    extension = replace(original, source="mcp", origin_id="untrusted-server")
    assert readonly_intent_issue(_intent(RequestAction.REVIEW), extension) is not None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Notify me after approval", frozenset({"system.notify"})),
        ('Explain "notify me" without doing it', frozenset()),
        ("Do not notify me", frozenset()),
        ("Review the notification code", frozenset()),
        ("请通知我", frozenset({"system.notify"})),
        ("不要通知我", frozenset()),
        ("> Notify me", frozenset()),
    ],
)
def test_system_actions_require_direct_unquoted_requests(text, expected):
    assert explicit_system_actions(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Analyze but do not modify files", ("mutation",)),
        ("先分析，别修改文件", ("mutation",)),  # noqa: RUF001
        ("Never execute commands", ("execution",)),
        ("不要通知我", ("notification",)),
        ('Explain "do not modify files"', ()),
        ("Review this:\n```\ndelete files\n```", ()),
    ],
)
def test_explicit_prohibitions_never_come_from_quoted_data(text, expected):
    assert intent_prohibitions(text) == expected


@pytest.mark.parametrize(
    "targets,path,allowed",
    [
        (("src/app.ts",), "src/app.ts", True),
        (("src/app.ts",), "src/other.ts", False),
        (("src/",), "src/app.ts", True),
        (("src/",), "src-other/app.ts", False),
        (("src/",), "src/../private.txt", False),
        (("current workspace",), "src/app.ts", True),
        (("current workspace",), "C:/private.txt", False),
        (("current workspace",), "../private.txt", False),
        (("the unclear thing",), "src/app.ts", False),
        ((), "src/app.ts", False),
    ],
)
def test_file_mutations_must_match_the_interpreted_target(targets, path, allowed):
    intent = _intent(RequestAction.CHANGE, targets=targets)
    assert (file_target_issue(intent, ({"path": path, "content": "new"},)) is None) is allowed
