from __future__ import annotations

from typing import Any

from app.memory.memory_injection_policy import MemoryInjectionPolicy
from app.memory.memory_schema import MemoryRetrievalBundle


def summarize_memories_for_prompt(bundle: MemoryRetrievalBundle, policy: MemoryInjectionPolicy) -> str:
    sections: list[str] = []
    if policy.include_profile:
        profile_lines = _format_key_value_lines(bundle.profile, policy.per_slot_budget.get("profile", 120))
        environment_lines = _format_key_value_lines(bundle.environment, max(80, policy.per_slot_budget.get("profile", 120) // 2))
        section_lines: list[str] = []
        if profile_lines:
            section_lines.append("User profile:")
            section_lines.extend(f"- {line}" for line in profile_lines)
        if environment_lines:
            section_lines.append("Environment:")
            section_lines.extend(f"- {line}" for line in environment_lines)
        if section_lines:
            sections.append("\n".join(section_lines))

    if policy.include_project and bundle.project:
        project_lines = _format_project_lines(bundle.project, policy.per_slot_budget.get("project", 140))
        if project_lines:
            sections.append("Current project context:\n" + "\n".join(f"- {line}" for line in project_lines))

    if policy.include_task and bundle.task:
        task_lines = _format_task_lines(bundle.task, policy.per_slot_budget.get("task", 120))
        if task_lines:
            sections.append("Current task state:\n" + "\n".join(f"- {line}" for line in task_lines))

    if policy.include_semantic and bundle.semantic:
        semantic_lines = _format_semantic_lines(bundle.semantic, policy.per_slot_budget.get("semantic", 220))
        if semantic_lines:
            sections.append("Relevant past experience:\n" + "\n".join(f"- {line}" for line in semantic_lines))

    if not sections:
        return ""

    joined = "[Relevant memory]\n" + "\n\n".join(sections)
    char_budget = max(240, int(policy.token_budget * 4.2))
    if len(joined) <= char_budget:
        return joined
    return joined[: char_budget - 18].rstrip() + "\n...[truncated memory]"


def _format_key_value_lines(items: list[dict[str, Any]], budget: int) -> list[str]:
    lines: list[str] = []
    remaining = budget
    for item in items:
        key = str(item.get("key", "") or "")
        value = str(item.get("value", "") or "")
        if key == "legacy_memory_migrated":
            continue
        line = value if key.startswith("pref_") else f"{key}: {value}".strip()
        if not line:
            continue
        if len(line) > remaining:
            break
        lines.append(line)
        remaining -= len(line) + 2
    return lines


def _format_project_lines(items: list[dict[str, Any]], budget: int) -> list[str]:
    lines: list[str] = []
    remaining = budget
    for item in items:
        line = f"[{item.get('module', '')}] {item.get('summary', '')}".strip()
        if not line:
            continue
        if len(line) > remaining:
            break
        lines.append(line)
        remaining -= len(line) + 2
    return lines


def _format_task_lines(items: list[dict[str, Any]], budget: int) -> list[str]:
    lines: list[str] = []
    remaining = budget
    for item in items:
        done_steps = item.get("done_steps") or []
        line = (
            f"goal={item.get('goal', '')}; step={item.get('current_step', '')}; "
            f"done={','.join(done_steps[-3:])}; blocked={item.get('blocked_reason', '')}; status={item.get('status', '')}"
        )
        line = line.strip()
        if len(line) > remaining:
            break
        lines.append(line)
        remaining -= len(line) + 2
    return lines


def _format_semantic_lines(items: list[Any], budget: int) -> list[str]:
    lines: list[str] = []
    remaining = budget
    for item in items:
        line = str(getattr(item, "content", "")).strip()
        if not line:
            continue
        if len(line) > remaining:
            break
        lines.append(line)
        remaining -= len(line) + 2
    return lines
