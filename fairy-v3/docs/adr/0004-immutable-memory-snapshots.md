# ADR 0004: Bind Immutable Memory Snapshots to Tasks

- Status: accepted
- Date: 2026-07-11

## Context

Canonical Hermes memory can change while a Task is running, and lexical
projections can lag, fail, or be rebuilt. Reading live memory on every model
turn would make one Task observe different facts over time, let a projection
refresh alter execution context, and make recovery or audit unable to reproduce
the original prompt inputs.

Search rows are not authoritative. They can contain stale or malicious synced
text and differ between SQLite FTS5 and PostgreSQL full-text search. A Task
therefore needs a stable context manifest assembled from canonical sources,
not a pointer to a live query.

## Decision

Core builds one Memory Snapshot while creating a Task and binds its ID and
SHA-256 content hash to that Task in the same Unit of Work. A Task may replay
the same binding, but it cannot replace it with a different Snapshot. Memory
writes and projection refreshes after that commit affect later Tasks only.

The lexical projection is generation- and watermark-fenced. A healthy
checkpoint means its generation matches the requested generation, its state is
`ready`, and its projected watermark is at least the Task's source ledger
cursor. Upserting documents and advancing a checkpoint are one transaction. A
failed refresh rolls back both; canonical memory remains committed in its own
transaction.

Every projection hit is resolved back to a tenant-scoped canonical Observation
or Claim revision and revalidated for namespace, Project, Conversation, Task,
Version provenance, lifecycle, valid time, tombstones, sensitivity, and content
scan state. Source kinds without a canonical resolver fail closed. Rendered
source text is escaped and labeled as data or untrusted data.

Candidate ordering is deterministic:

1. exact Project Canonical Claims;
2. remaining Project Canonical Claims;
3. exact User Profile Claims;
4. remaining User Profile Claims;
5. Conversation Draft Claims;
6. selected Observation history.

Within one category, authority is ordered `deterministic_core`,
`accepted_version`, `explicit_user`, then `model_suggestion`. Ties use exact
match, lexical score, source cursor, confidence, source kind, source ID, and
source revision. Live conflicting Project Canonical alternatives and exact
canonical matches are mandatory.

The default budget is 2,400 conservative UTF-8 bytes with a hard ceiling of
3,000. Section budgets are 400 for User Profile, 1,000 for Project Canonical,
500 for Conversation Draft, and 500 for history. Unused capacity flows only
downward. Mandatory canonical material may consume the default reserve up to
the hard ceiling; exceeding that ceiling raises
`MEMORY_SNAPSHOT_TOO_LARGE`.

The Snapshot hash is computed from canonical JSON containing:

- Snapshot and policy versions;
- source and projection watermarks;
- projection generation and state;
- ready/degraded status and degraded reason;
- every ordered item's source kind, ID, revision, namespace, selection reason,
  authority, score components, rendered-text hash, and token count.

Scope IDs live on the immutable Snapshot row and Task binding, while the hash
captures the reusable content manifest. Persistence verifies item hashes and
the aggregate hash on every restore.

If projection health or search fails, Core does not reuse an older Snapshot or
invent search scores. It selects bounded recent canonical Observations,
produces a `degraded` Snapshot with a stable reason, and continues with an
auditable context. The public API exposes task-scoped search, bound Snapshot
inspection, and projection health; callers cannot supply tenant or Scope
authority.

## Consequences

- Task execution and recovery can reproduce the exact selected memory context.
- Projection freshness improves later Tasks without changing work already in
  progress.
- SQLite and PostgreSQL may rank lexical candidates differently internally,
  but Core applies the same canonical resolution, policy, ordering, and budget.
- Snapshot rows and ordered items add durable storage, which is accepted for
  auditability and deterministic replay.
- Episodes, semantic expansion, parallel generation switching, asynchronous
  rebuild workers, and multi-device tombstone propagation remain compatible
  additions because they must produce candidates for the same immutable
  Snapshot boundary.
