# ADR 0014: Manage Knowledge Through Task-Scoped Core Projections

- Status: Accepted
- Date: 2026-07-12

## Context

Documents, lexical RAG projections, and Hermes Memory were available to Agent
tools and CoreClient, but the desktop exposed no direct way to inspect, search,
delete, or forget them. Treating a document index as the Memory authority would
also collapse Hermes observations, claims, revisions, and tombstones into RAG.

## Decision

The desktop Knowledge panel is a Task-scoped Core projection. It receives no
database handle and sends no caller-selected Project, Conversation, or Version
scope. The workspace model injects the selected durable Task into document and
Memory calls.

Documents are listed and searched through the managed document APIs. Deletion
requires an explicit desktop confirmation and a second `user_confirmed` Core
check. Hermes Memory search returns rebuildable search documents, but forgetting
targets the canonical observation or claim source through `memory.forget`; it
does not delete a search row. Forgetting also requires explicit confirmation and
creates a durable tombstone.

Skills and MCP remain in the governed Extension registry. MCP discovery and
schema acceptance continue to require a durable Task and Core-owned policies.

## Consequences

- Users can inspect and remove scoped knowledge without asking the model.
- RAG remains a rebuildable document projection, not the Memory authority.
- The Renderer cannot query another Task by supplying arbitrary Scope IDs.
- Destructive operations remain durable, idempotent, and auditable in Core.
