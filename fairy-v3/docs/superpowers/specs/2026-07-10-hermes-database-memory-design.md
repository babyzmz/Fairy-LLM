# Fairy V3 Hermes Database Memory Design

- Status: approved design
- Date: 2026-07-10
- Scope: Core persistence, local/cloud memory, synchronization, retrieval, and prompt snapshots

## Intent

Fairy adopts the useful Hermes memory pattern as a product behavior, not as a
runtime dependency. The database must remember structured facts, full session
history, and task experience. Vector retrieval is one replaceable projection;
it is never the source of truth and is never the only way Fairy remembers.

The design extends the Hermes Agent model of bounded curated memory and
searchable session history to Fairy's Project, Conversation, Task, Version,
Scope, and multi-device contracts. The reference behavior is documented at
<https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/>.

## Goals

1. Preserve authoritative project facts, user preferences, session history,
   and execution experience across devices.
2. Keep every memory item attributable to a durable source event and immutable
   Scope IDs.
3. Provide deterministic exact and full-text recall before semantic recall.
4. Build bounded, immutable prompt snapshots that do not change during a Task.
5. Detect conflicts, staleness, prompt injection, secret egress, and scope
   leakage before memory enters a model context.
6. Keep FTS and embedding indexes rebuildable from canonical relational data.
7. Use SQLite locally and PostgreSQL in the cloud behind the same ports.

## Non-goals

- Storing hidden reasoning or model chain-of-thought.
- Letting a model, renderer, Pet, or Companion write memory directly.
- Treating embeddings, generated summaries, or access frequency as facts.
- Migrating the legacy Fairy database or importing legacy memory code.
- Introducing Redis, NATS, a graph database, or a separate vector service.
- Silently merging contradictory project facts.

## Memory layers

### Working context

The active request, Conversation messages, ScopeContract, selected artifacts,
and current Task events form working context. This layer is assembled for a
turn but is not promoted to durable memory by default.

### Curated memory

Curated memory is the bounded Hermes-style layer injected into prompts. It is
assembled from versioned claims and split into these authorities:

1. `project_canonical`: accepted project facts and decisions.
2. `conversation_draft`: unaccepted facts visible only to one Conversation.
3. `user_profile`: preferences and stable personal facts.
4. `device_local`: device capabilities and local-only operational facts.

Authority is contextual. `project_canonical` overrides a conflicting personal
preference for project behavior. `conversation_draft` never overrides
canonical project state outside its Conversation. Device-local facts never
sync unless an explicit policy promotes them.

### Episodic memory

Task episodes record the goal, Scope digest, selected tools, commands, result,
errors, artifacts, accepted Version, user feedback, and distilled reusable
lesson. Episodes remember strategies and failures without pretending they are
current project facts.

### Searchable history

Conversation messages and user-visible ledger events remain in their canonical
tables. A full-text projection makes their original content searchable with
source IDs and cursors. Search results never become curated claims merely by
being retrieved.

## Invariants

1. Every row is tenant-scoped. Project-owned rows also carry Project ID and,
   where applicable, Conversation, Task, and Version IDs.
2. Every Observation, Claim revision, Episode, and Snapshot has provenance.
3. A Claim is mutable only by appending a revision, superseding it, expiring
   it, or recording a tombstone.
4. Project Canonical claims are promoted only from accepted Versions, explicit
   user decisions, or deterministic Core facts.
5. A Task binds one immutable Memory Snapshot ID and content hash alongside its
   Scope digest.
6. FTS and vector rows are projections and may be deleted and rebuilt.
7. Projection failure cannot roll back a committed canonical fact.
8. Forget operations create durable tombstones before asynchronous physical
   deletion.
9. Companion code may consume public Presence projections but cannot retrieve,
   promote, or mutate memory.
10. No memory payload contains hidden reasoning.

## Relational model

### `memory_observations`

Append-only candidate material extracted from user-visible messages, accepted
artifacts, command results, and explicit user memory actions.

Required fields include tenant and Scope IDs, source event ID and cursor,
source type, original content, content hash, proposed namespace, confidence,
sensitivity, injection-scan result, lifecycle status, and timestamps.

An Observation is evidence, not a fact.

### `memory_claims`

Stable identity for a structured fact. It stores tenant and namespace scope,
subject, predicate, current revision number, conflict-set ID, lifecycle state,
and timestamps.

### `memory_claim_revisions`

Immutable revisions containing a typed JSON value, normalized searchable text,
source Observation IDs, source event IDs, authority, confidence, valid-time
interval, recorded time, superseded revision, and actor.

The unique key is `(tenant_id, claim_id, revision)`. A conditional uniqueness
constraint permits one current revision per claim.

### `memory_episodes`

Immutable Task outcomes containing request summary, Scope digest, tool and
CommandRun IDs, terminal state, error codes, Artifact and Version IDs, user
feedback, reusable lesson, quality score, and timestamps. Raw command logs stay
in the ledger and are referenced rather than duplicated.

### `memory_snapshots`

Immutable prompt-context manifests containing tenant and Scope IDs, Snapshot
version, policy version, source watermark cursor, content hash, token count,
status, and creation time.

### `memory_snapshot_items`

Ordered references to Claim revisions, Episodes, and selected history records.
Each item records why it was selected, authority, score components, rendered
text hash, and token count. Snapshot content can therefore be reproduced and
audited without storing opaque model context.

### `memory_search_documents`

Rebuildable lexical projection with source kind and ID, namespace, Scope IDs,
language, normalized text, source revision or cursor, projection generation,
and update time.

SQLite uses an FTS5 external-content virtual table. PostgreSQL uses a generated
`tsvector` and tenant/scope-aware GIN indexes.

### `memory_embeddings`

Rebuildable semantic projection with source kind and ID, source content hash,
embedding model ID, dimensions, projection generation, vector payload, and
creation time. A content hash and model ID uniquely identify an embedding.

PostgreSQL development uses pgvector 0.8.2 built into the pinned PostgreSQL
18.4 image. The vector extension is optional at runtime: exact and FTS recall
continue when it is unavailable. Local SQLite initially stores compact float
vectors and performs an exact scan only over a bounded FTS/scope shortlist. A
signed packaged SQLite vector extension may replace that adapter later without
changing Core contracts.

### `memory_access_log`

Append-only record of retrieval candidates, selected items, rejection reasons,
snapshot ID, downstream acceptance or correction, and latency. It supports
quality evaluation and decay but is not model-visible by default.

### `memory_projection_checkpoints`

Tracks each projection generation, source watermark, model/schema version,
state, retry count, and last error. A new generation is built alongside the
active generation and switched atomically after validation.

## Write pipeline

```text
Durable user-visible event
  -> memory.observe Command
  -> append Observation in Core Unit of Work
  -> scope, sensitivity, injection, and provenance policy
  -> deterministic fact or user-approved promotion
  -> append Claim revision or Episode
  -> append memory domain event and transactional outbox
  -> commit
  -> asynchronous FTS and embedding projection
```

Models may propose Observations or Claim changes through registered tools. The
Core injects tenant and Scope identity, recomputes content hashes, scans the
content, enforces authority, and decides whether approval is required. Model
supplied IDs, namespaces, confidence, and provenance are untrusted.

The initial commands are:

- `memory.observe`
- `memory.claim.promote`
- `memory.claim.supersede`
- `memory.claim.resolve_conflict`
- `memory.episode.record`
- `memory.forget`
- `memory.projection.rebuild`

Every command uses the shared Command Bus, idempotency fingerprint, policy
matrix, fenced execution, Event Ledger, and outbox.

## Conflict and lifecycle rules

Claims can be `candidate`, `active`, `conflicted`, `superseded`, `expired`,
`rejected`, or `forgotten`.

When incompatible values overlap in valid time, the Core creates or reuses a
conflict set. It retains both revisions and emits a user-visible conflict event
when the conflict affects current work. Authority may select a value for one
Snapshot, but it never deletes or silently merges the other value. Explicit
user resolution appends a new revision referencing all resolved revisions.

Time-sensitive facts carry `valid_from` and `valid_to`. Expiry removes them
from new Snapshots but preserves provenance. Decay can lower episodic ranking;
it cannot demote canonical facts. Forgetting first writes a tombstone, removes
the item from active projections, propagates the tombstone through sync, and
physically deletes eligible payloads after the configured retention period.

## Retrieval pipeline

```text
Scope and tenant filter
  -> exact typed Claim lookup
  -> project canonical selection
  -> FTS history and Claim search
  -> relevant Task Episodes
  -> optional vector expansion
  -> validity, conflict, sensitivity, and injection checks
  -> authority-aware ranking
  -> bounded immutable Snapshot
```

Ranking is explainable and records separate authority, exact-match, lexical,
semantic, recency, confidence, feedback, and decay components. A semantic
score can broaden candidates but cannot outrank an exact current canonical
fact solely because its vector similarity is higher.

If projections are stale or unavailable, retrieval falls back to relational
Claims and bounded recent history. It returns an explicit degraded status and
never fabricates remembered context.

## Snapshot budgets

The default prompt budget is 2,400 tokens with a hard ceiling of 3,000:

- user profile: 400 tokens
- project canonical: 1,000 tokens
- Conversation summary and draft: 500 tokens
- retrieved Episodes and history: 500 tokens

Unused budget can move downward in that order, but lower-authority sections
cannot evict required canonical facts. Snapshot assembly is deterministic for
the same watermark, Scope, policy version, and projection generation.

Memory written during a Task is not visible to that Task after its Snapshot is
bound. A later turn or resumed Task receives a new Snapshot only through an
explicit Core transition.

## Ports and application services

Core defines these transport-independent interfaces:

- `MemoryRepository`: Observations, Claims, revisions, Episodes, tombstones.
- `MemorySearchIndex`: lexical and semantic projection reads.
- `MemoryProjectionWriter`: idempotent projection updates by generation.
- `MemorySnapshotBuilder`: policy-driven retrieval and immutable assembly.
- `MemoryPolicy`: scope, authority, sensitivity, injection, and approval.

`CoreUnitOfWork` exposes `memory` beside StateStore, CommandLedger, Event
Ledger, and outbox writers on one database transaction. External projection
work happens after commit through outbox jobs.

The public CoreClient gains scoped read/search, Claim inspection, conflict
resolution, explicit remember/forget, Snapshot inspection, and projection
health methods. Developer-only diagnostics expose score components and source
IDs but never hidden reasoning.

## Local and cloud storage

Local SQLite and cloud PostgreSQL pass one MemoryRepository contract suite.

SQLite:

- one tenant per database, with the same tenant columns retained for parity;
- WAL mode, foreign keys, busy timeout, schema migrations, and FTS5;
- bounded exact vector scan adapter;
- local-only data excluded from sync by namespace policy.

PostgreSQL:

- shared schema with `(tenant_id, id)` keys, composite foreign keys, explicit
  predicates, and forced row level security;
- PostgreSQL full-text search with GIN indexes;
- pgvector as a rebuildable semantic projection;
- global Event Ledger cursor and transactional outbox;
- `SKIP LOCKED` projection workers with owner, expiry, and fencing token.

The Docker image remains deterministic: build pgvector 0.8.2 from pinned
source and checksum on top of `postgres:18.4-alpine3.24`. Do not use a rolling
pgvector image tag. Docker Compose still has no Redis, NATS, or Docker socket.

## Synchronization

Devices synchronize canonical memory events, Claim revision manifests,
Snapshot manifests when required for audit, and tombstones. They do not copy a
SQLite database, FTS table, or vector index.

Server cursors provide ordering. Idempotency uses tenant-scoped event IDs plus
payload fingerprints. Concurrent incompatible Claim revisions remain in one
conflict set. Projection generations are rebuilt independently on each device
or in the cloud from canonical events.

## Security and privacy

- Memory text is scanned for prompt injection, secret exfiltration patterns,
  invisible Unicode controls, and disallowed binary content before promotion.
- Secrets are represented by references to a secret store, never copied into a
  Claim, Snapshot, embedding, log, or outbox payload.
- Sensitive namespaces can disable cloud sync and semantic embedding.
- RLS and application predicates enforce tenant isolation.
- Snapshot rendering escapes untrusted content and marks quoted source text as
  data, not instructions.
- User-visible provenance explains what Fairy remembered and why.
- Retention and forget operations are testable across database, S3 artifacts,
  search projections, device sync, and backups.

## Stable errors and events

New stable errors:

- `MEMORY_SCOPE_VIOLATION`
- `MEMORY_CONFLICT`
- `MEMORY_INJECTION_BLOCKED`
- `MEMORY_SECRET_BLOCKED`
- `MEMORY_PROJECTION_STALE`
- `MEMORY_SNAPSHOT_TOO_LARGE`
- `MEMORY_FORGOTTEN`

Key user/developer events include Observation accepted/rejected, Claim
promoted/superseded/conflicted/resolved/expired/forgotten, Episode recorded,
Snapshot built, and projection generation started/ready/failed. Internal
ranking traces stay developer-visible and contain no chain-of-thought.

## Verification

1. State-machine and property tests cover Claim revision, conflict, expiry,
   tombstone, and Snapshot transitions.
2. Contract tests run against SQLite and real PostgreSQL.
3. Cross-tenant tests reuse identical IDs, idempotency keys, content hashes,
   and vector payloads without leakage.
4. Scope tests prove Conversation Draft and device-local facts cannot enter an
   unrelated Snapshot.
5. Concurrency tests cover duplicate promotions, conflicting devices,
   projection generation switches, worker lease expiry, and stale fences.
6. Recovery tests crash after every canonical write/outbox/projection stage and
   prove canonical memory remains intact and projections rebuild.
7. Retrieval tests cover exact fact precedence, FTS, optional vector expansion,
   conflict disclosure, expired facts, degraded projection fallback, and fixed
   Snapshot hashes.
8. Security tests cover prompt injection, Unicode controls, secret material,
   malicious synced events, RLS, and forget propagation.
9. Performance gates measure Snapshot build latency, FTS p95, projection lag,
   initial database size, and bounded prompt tokens.

## Delivery slices

1. Canonical schema, ports, Unit of Work, tenant/RLS, Claim lifecycle, and
   SQLite/PostgreSQL contract tests.
2. Full-text history, Snapshot builder, prompt budgets, provenance, and
   degraded fallback.
3. Episodes, outbox projection workers, pgvector Docker support, semantic
   expansion, and projection generation rebuilds.
4. Multi-device conflict/tombstone sync, user controls, diagnostics, security,
   recovery, and performance gates.
