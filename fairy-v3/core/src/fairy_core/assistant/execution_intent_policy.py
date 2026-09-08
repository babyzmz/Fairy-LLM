from __future__ import annotations

import re

from fairy_core.assistant.execution_intent import ExecutionIntentSnapshot
from fairy_core.assistant.interpretation import (
    InputSegmentKind,
    InterpretationConfidence,
    InterpretationDisposition,
    RequestAction,
    segment_user_input,
)
from fairy_core.commanding.registry import SideEffect, ToolDefinition

_RESPONSE_ACTIONS = frozenset({RequestAction.ANSWER, RequestAction.EXPLAIN, RequestAction.REVIEW})
# These builtins persist bounded planning/evidence, not edits or external messages.
# Match executor and source as well as name: an extension cannot inherit this exception.
_INTERNAL_WRITES = {
    "research.build": "research_application",
    "execution.plan": "project_tools",
}
_WORKSPACE_TOOLS = frozenset(
    {
        "edit.propose_changeset",
        "deps.install",
        "review.typecheck",
        "review.lint",
        "review.test",
        "review.build",
        "run.sandboxed",
    }
)
_ACTION_TOOLS = {
    RequestAction.CHANGE: _WORKSPACE_TOOLS,
    RequestAction.CREATE: _WORKSPACE_TOOLS,
    RequestAction.RUN: _WORKSPACE_TOOLS - {"edit.propose_changeset", "deps.install"},
    RequestAction.GENERATE: frozenset(
        {
            "media.images.generate",
            "media.audio.generate",
            "media.videos.start",
        }
    ),
    RequestAction.BROWSE: frozenset(
        {
            "browser.click",
            "browser.fill",
            "browser.press",
            "browser.select",
            "browser.check",
            "browser.download",
        }
    ),
    RequestAction.MANAGE: frozenset(
        {
            "memory.suggest",
            "system.notify",
            "system.copy_text",
            "system.reveal_path",
            "system.open_settings",
            "system.open_url",
        }
    ),
}


class ExecutionIntentError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.error_code = code
        self.model_detail = (
            "The current request does not authorize this operation. Continue with read-only "
            "evidence or ask the user to clarify the intended action and target."
        )
        super().__init__(code)


def intent_prohibitions(source: str) -> tuple[str, ...]:
    actionable = " ".join(
        segment.text
        for segment in segment_user_input(source)
        if segment.kind is InputSegmentKind.TEXT
    )
    negation = r"(?:\bdo not\s+|\bdon't\s+|\bnever\s+|不要|不得|禁止|别)"
    actions = {
        "mutation": r"(?:modify|change|edit|write|delete)\b|修改|更改|改动|写入|删除",
        "execution": r"(?:execute|run)\b|执行|运行",
        "notification": r"notify\b|通知|提醒",
        "memory": r"(?:remember|save memories)\b|保存记忆|记住",
        "publication": r"(?:publish|send|post)\b|发布|发送",
    }
    return tuple(
        action
        for action, pattern in actions.items()
        if re.search(rf"{negation}(?:{pattern})", actionable, re.IGNORECASE)
    )


def readonly_intent_issue(
    intent: ExecutionIntentSnapshot | None,
    definition: ToolDefinition,
) -> str | None:
    if definition.side_effect in {SideEffect.NONE, SideEffect.READ}:
        return None
    if (
        definition.source == "builtin"
        and _INTERNAL_WRITES.get(definition.name) == definition.executor
    ):
        return None
    if intent is None:
        return "EXECUTION_INTENT_UNAVAILABLE"
    prohibitions = set(intent.source_prohibitions)
    for constraint in intent.user_constraints:
        prohibitions.update(intent_prohibitions(constraint))
    if (
        "mutation" in prohibitions
        or ("execution" in prohibitions and definition.side_effect is SideEffect.EXECUTE)
        or ("notification" in prohibitions and definition.name == "system.notify")
        or ("memory" in prohibitions and definition.name == "memory.suggest")
    ):
        return "EXECUTION_INTENT_PROHIBITED"
    if (
        intent.action in _RESPONSE_ACTIONS
        or intent.confidence is InterpretationConfidence.LOW
        or intent.disposition is InterpretationDisposition.CLARIFICATION_REQUIRED
    ):
        return "EXECUTION_INTENT_READ_ONLY"
    if definition.source != "builtin":
        return "EXECUTION_INTENT_EXTENSION_UNDECLARED"
    if (
        intent.target_descriptions
        and all(target.startswith("system.") for target in intent.target_descriptions)
        and definition.name not in intent.target_descriptions
    ):
        return "EXECUTION_INTENT_TARGET_MISMATCH"
    if definition.name not in _ACTION_TOOLS.get(intent.action, ()):
        return "EXECUTION_INTENT_ACTION_MISMATCH"
    return None


def file_target_issue(intent: ExecutionIntentSnapshot, files) -> str | None:
    """Match explicit Workspace-relative targets; Scope still owns actual path access."""
    if not isinstance(files, (list, tuple)) or not files:
        return "EXECUTION_INTENT_TARGET_UNRESOLVED"
    workspace_aliases = {
        "current workspace",
        "current project",
        "workspace",
        "project",
        "当前工作区",
        "当前项目",
        str(intent.workspace_id),
    }
    if intent.project_id is not None:
        workspace_aliases.add(str(intent.project_id))
    targets = tuple(target.strip() for target in intent.target_descriptions)
    whole_workspace = any(target.casefold() in workspace_aliases for target in targets)
    for item in files:
        path = item.get("path") if isinstance(item, dict) else None
        if not _relative_target(path):
            return "EXECUTION_INTENT_TARGET_MISMATCH"
        if whole_workspace:
            continue
        if not any(
            _relative_target(target.rstrip("/"))
            and (path == target or (target.endswith("/") and path.startswith(target)))
            for target in targets
        ):
            return (
                "EXECUTION_INTENT_TARGET_UNRESOLVED"
                if not targets
                else ("EXECUTION_INTENT_TARGET_MISMATCH")
            )
    return None


def _relative_target(value) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and not any(character in value for character in "\\:*?\x00")
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )


__all__ = ["ExecutionIntentError", "intent_prohibitions", "readonly_intent_issue"]
