from __future__ import annotations

from fairy_core.commanding.registry import SlashCommandDefinition


def default_slash_commands() -> tuple[SlashCommandDefinition, ...]:
    return (
        SlashCommandDefinition(
            name="new",
            description="Start a new durable conversation.",
            required_operation="workspace.create_scratch",
        ),
        SlashCommandDefinition(
            name="project",
            description="Switch to the current Project workspace.",
        ),
        SlashCommandDefinition(
            name="permission",
            description="Change the device execution profile.",
            argument_hint="<observe|standard|autonomous>",
        ),
        SlashCommandDefinition(
            name="stop",
            description="Stop the active assistant response.",
        ),
        SlashCommandDefinition(
            name="clear",
            description="Continue in a new durable conversation.",
            required_operation="workspace.create_scratch",
        ),
        SlashCommandDefinition(
            name="help",
            description="Show available explicit commands.",
        ),
    )


__all__ = ["default_slash_commands"]
