from __future__ import annotations

import json

from fairy_core.assistant.application import AssistantApplication
from fairy_core.assistant.context import _knowledge_context, _memory_context
from fairy_core.assistant.durable_context import project_tool_context
from fairy_core.domain.ids import new_id
from fairy_core.knowledge.models import KnowledgeRevision
from fairy_core.memory.models import MemoryAuthority, MemoryNamespace
from fairy_core.memory.retrieval_models import (
    MemorySelectionReason,
    MemorySnapshotItem,
    MemorySourceKind,
)
from fairy_core.providers import ModelMessage, ModelRole, ModelToolCall


def test_tool_projection_keeps_protocol_identity_without_replaying_large_payloads() -> None:
    source = "const generated = true;\n" * 2_000
    messages = (
        ModelMessage.create(
            role=ModelRole.ASSISTANT,
            content="",
            tool_calls=(
                ModelToolCall.create(
                    tool_call_id="call-edit",
                    name="edit.propose_changeset",
                    arguments=json.dumps(
                        {
                            "files": [{"path": "main.js", "content": source}],
                            "reason": "Generate the page",
                        }
                    ),
                ),
                ModelToolCall.create(
                    tool_call_id="call-skill",
                    name="skill.gsap-core",
                    arguments='{"request":"Use GSAP"}',
                ),
            ),
        ),
        ModelMessage.create(
            role=ModelRole.TOOL,
            content="Skill guidance\n" + ("important details\n" * 2_000),
            name="skill.gsap-core",
            tool_call_id="call-skill",
        ),
        ModelMessage.create(
            role=ModelRole.TOOL,
            content='{"status":"applied","files":["main.js"]}',
            name="edit.propose_changeset",
            tool_call_id="call-edit",
        ),
    )

    projection = project_tool_context(messages)

    assert projection.source_characters > projection.projected_characters
    assert projection.projected_characters <= 40_000
    assert projection.truncated_items >= 2
    assert source not in projection.messages[0].tool_calls[0].arguments
    assert "omitted sha256=" in projection.messages[0].tool_calls[0].arguments
    assert projection.messages[1].tool_call_id == "call-skill"
    assert projection.messages[2].tool_call_id == "call-edit"


def test_harness_projects_bounded_memory_and_knowledge_catalogs() -> None:
    memory_text = "Remember this preference. " * 1_000
    memory = MemorySnapshotItem.create(
        ordinal=0,
        source_kind=MemorySourceKind.OBSERVATION,
        source_id=new_id(),
        source_revision=None,
        namespace=MemoryNamespace.USER_PROFILE,
        selection_reason=MemorySelectionReason.USER_PROFILE,
        authority=MemoryAuthority.EXPLICIT_USER,
        score_components={"lexical": 1.0},
        rendered_text=memory_text,
        token_count=4_000,
    )
    note_body = "Private Obsidian note body. " * 2_000
    revision = KnowledgeRevision.create(
        item_id=new_id(),
        source_id=new_id(),
        project_id=new_id(),
        revision=1,
        relative_path="Design/Animation.md",
        title="Animation system",
        kind="markdown",
        content=note_body,
        links=("Motion tokens",),
        frontmatter={},
        provenance={"connector": "obsidian"},
        source_cursor=1,
    )

    memory_projection = _memory_context((memory,))
    knowledge_projection = _knowledge_context((revision,))

    assert len(memory_projection.content) <= 12_000
    assert "memory projection truncated" in memory_projection.content
    assert knowledge_projection.source_characters == len(note_body)
    assert note_body not in knowledge_projection.content
    assert "Design/Animation.md" in knowledge_projection.content
    assert str(revision.id) in knowledge_projection.content
    assert "use knowledge.search/read" in knowledge_projection.content


def test_reviewer_receives_only_bounded_conversation_messages() -> None:
    messages = (
        ModelMessage.create(role=ModelRole.SYSTEM, content="system policy" * 2_000),
        ModelMessage.create(role=ModelRole.USER, content="Build the page"),
        ModelMessage.create(
            role=ModelRole.ASSISTANT,
            content="",
            tool_calls=(
                ModelToolCall.create(
                    tool_call_id="call-edit",
                    name="edit.propose_changeset",
                    arguments='{"files":[]}',
                ),
            ),
        ),
        ModelMessage.create(
            role=ModelRole.TOOL,
            content="large tool result" * 3_000,
            name="edit.propose_changeset",
            tool_call_id="call-edit",
        ),
        ModelMessage.create(role=ModelRole.ASSISTANT, content="Earlier useful answer"),
    )

    projected = AssistantApplication._review_source_messages(messages)

    assert [message.role for message in projected] == [ModelRole.USER, ModelRole.ASSISTANT]
    assert all(not message.tool_calls and message.tool_call_id is None for message in projected)
    assert sum(len(message.content) for message in projected) <= 24_000
