from __future__ import annotations

import re
from html import escape

from fairy_core.memory.models import MemoryClaim, MemoryClaimRevision, MemoryObservation

_QUERY_TOKEN = re.compile(r"\w+", re.UNICODE)


def render_claim(
    *,
    claim: MemoryClaim,
    revision: MemoryClaimRevision,
    conflict: bool,
) -> str:
    conflict_line = "\nCONFLICT: live alternative; do not silently resolve." if conflict else ""
    return (
        '<memory-source kind="claim_revision" role="data" '
        f'namespace="{claim.namespace.value}" authority="{revision.authority.value}">'
        f"{conflict_line}\nsubject: {escape(claim.subject, quote=True)}"
        f"\npredicate: {escape(claim.predicate, quote=True)}"
        f"\nvalue: {escape(revision.normalized_text, quote=True)}"
        "\n</memory-source>"
    )


def render_observation(observation: MemoryObservation) -> str:
    return (
        '<memory-source kind="observation" role="untrusted-data" '
        f'namespace="{observation.proposed_namespace.value}" '
        f'authority="{observation.authority.value}">\n'
        f"{escape(observation.content, quote=True)}\n"
        "</memory-source>"
    )


def is_exact_query_match(query: str, content: str) -> bool:
    normalized_query = query.casefold().strip()
    normalized_content = content.casefold()
    if normalized_query in normalized_content:
        return True
    query_tokens = set(_QUERY_TOKEN.findall(normalized_query))
    content_tokens = set(_QUERY_TOKEN.findall(normalized_content))
    return bool(query_tokens) and query_tokens.issubset(content_tokens)


__all__ = ["is_exact_query_match", "render_claim", "render_observation"]
