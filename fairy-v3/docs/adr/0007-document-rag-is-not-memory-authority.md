# ADR 0007: Keep Document RAG Separate from Hermes Memory Authority

- Status: accepted
- Date: 2026-07-11

## Context

Managed documents and Hermes memory both provide text to an assistant, but
they have different ownership and lifecycle rules. Treating document chunks or
embedding hits as memory would let a disposable index silently create durable
user facts, leak Conversation documents into Project scope, or replace a
Task-bound Snapshot after execution starts.

## Decision

Hermes relational rows are the only memory authority. Observations, Claims,
Claim revisions, Tombstones, provenance, authority, sensitivity, and
acceptance state are canonical SQLite/PostgreSQL data. A Task binds one
immutable Hermes Snapshot ID and hash before model execution.

Documents are a separate governed corpus. Core stores tenant-scoped document
metadata, immutable revisions, visibility, integrity hashes, and chunk
locators. Local bytes live in the managed document directory and Cloud bytes
live in tenant-bound S3-compatible storage. Search results are bounded,
source-labelled RAG context for the current Task; they cannot write or promote
a Hermes Claim.

Lexical FTS and any later embedding index are disposable projections. Every
result is resolved back to canonical rows, rechecked against tenant, Project,
Conversation, Task, Version, visibility, integrity, and deletion state, then
escaped and labelled as untrusted data. Document deletion and memory forgetting
remain different Commands and events.

Moving a document fact into long-term memory requires an explicit Hermes
Observation or Claim promotion through the Command Bus and normal policy. No
automatic RAG-to-memory bridge exists.

## Consequences

- Rebuilding chunks, FTS, or embeddings cannot alter user memory.
- Conversation-only documents do not leak into another Conversation or Project
  canonical context.
- Document citations remain reproducible by revision and content hash.
- Memory forgetting does not erase source documents, and document deletion does
  not rewrite accepted Claims; each lifecycle remains auditable.
- A future vector adapter may improve retrieval, but it cannot become a second
  memory database.

## Verification

Boundary checks reject known standalone vector-database dependencies in Core
composition. Unit and PostgreSQL integration tests cover document RLS,
revision/integrity checks, deletion, scoped search, Hermes Snapshot binding,
and the absence of document payloads from canonical Memory Claims.
