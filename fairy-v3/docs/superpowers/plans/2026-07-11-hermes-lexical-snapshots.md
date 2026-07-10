# Hermes Lexical Retrieval and Snapshots Implementation Plan

> **Execution mode:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` and implement this plan inline, task-by-task. Do not dispatch subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic full-text memory recall and immutable, provenance-complete Memory Snapshots that are bound once to each Task and remain usable when projections are unavailable.

**Architecture:** Canonical Claims, Observations, and durable user-visible events remain the source of truth. Dialect-specific lexical indexes are rebuildable projections behind Core ports; a policy-driven builder combines exact scoped Claims with lexical history into an immutable Snapshot, and falls back to relational data with an explicit degraded status whenever projection health is insufficient.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0.51+, SQLite FTS5, PostgreSQL 18.4 generated `tsvector`/GIN, Alembic 1.18+, Pydantic 2.13+, FastAPI 0.139.x, React/TypeScript generated contracts, pytest 9.1+, Hypothesis 6.156+, Docker Compose.

## Global Constraints

- V3 cannot import legacy root `app/`, `fairy-desktop/`, `skills/`, `lazy_runtime/`, or `legacy_surface/` modules.
- Project-first, Task-driven, Preview-first; chat history is evidence, not project state.
- Every executable side effect crosses the Command Bus; projection maintenance is durable and cannot overwrite canonical memory.
- Every database-owned row is tenant-scoped; PostgreSQL also enforces forced row level security.
- A Task binds at most one immutable Memory Snapshot ID and SHA-256 content hash; normal Task execution cannot replace either value.
- Snapshot identity, Scope IDs, source watermark, policy version, projection generation, and content hashes are injected or recomputed by Core.
- The default Snapshot budget is 2,400 tokens and the hard ceiling is 3,000 tokens.
- Section budgets are `user_profile=400`, `project_canonical=1000`, `conversation_draft=500`, and `history=500`; unused budget flows only to later sections.
- Required exact current Project Canonical facts may consume unused lower-priority capacity but cannot be evicted by lower-authority content.
- Conversation Draft, device-local, and Project Canonical data must obey ScopeContract visibility and cannot leak across tenants, Projects, Conversations, Versions, or devices.
- Expired, forgotten, rejected, injection-blocked, and secret-egress-blocked material cannot enter a new Snapshot.
- Conflicted current Claims remain visible as an explicit conflict set; retrieval never silently merges or deletes alternatives.
- Snapshot assembly is deterministic for the same Scope, query, source watermark, policy version, projection generation, and canonical rows.
- Projection failure cannot roll back a committed Observation, Claim revision, tombstone, Task, or Version.
- Snapshot items expose source IDs and score components but never hidden reasoning or chain-of-thought.
- SQLite uses an FTS5 external-content table; PostgreSQL uses a generated `tsvector` plus tenant/scope-aware GIN/B-tree indexes.
- Cloud remains brokerless: PostgreSQL lease/outbox, no Redis, no NATS, and no Docker socket.
- Docker-unavailable runs must report PostgreSQL/S3 integration as skipped, never as passed.

---

### Task 10: Define Snapshot, Search, and Task-binding Domain Contracts

**Files:**
- Create: `fairy-v3/core/src/fairy_core/memory/retrieval_models.py`
- Create: `fairy-v3/core/src/fairy_core/memory/retrieval_ports.py`
- Modify: `fairy-v3/core/src/fairy_core/memory/__init__.py`
- Modify: `fairy-v3/core/src/fairy_core/domain/models.py`
- Modify: `fairy-v3/core/src/fairy_core/contracts/models.py`
- Test: `fairy-v3/core/tests/test_memory_retrieval_domain.py`
- Test: `fairy-v3/core/tests/test_domain.py`
- Test: `fairy-v3/core/tests/test_contracts.py`

**Interfaces:**
- Produces: `MemorySourceKind`, `MemorySnapshotStatus`, `ProjectionState`, and `MemorySelectionReason` string enums.
- Produces: immutable `MemorySearchDocument`, `MemorySearchHit`, `MemorySnapshotItem`, `MemorySnapshot`, and `MemoryProjectionHealth` dataclasses.
- Produces: `MemorySearchIndex`, `MemoryProjectionWriter`, `MemorySnapshotRepository`, and `MemorySnapshotBuilder` protocols.
- Produces: `Task.bind_memory_snapshot(snapshot_id: UUID, content_hash: str) -> None` with one-time binding semantics.
- Extends: `ScopeContract.memory_snapshot_id` and `ScopeContract.memory_snapshot_hash`; both values participate in `scope_digest`.

- [ ] **Step 1: Write red domain tests for immutable binding, canonical hashing, validation, and Scope digest inclusion**

```python
snapshot = MemorySnapshot.create(
    project_id=scope.project_id,
    conversation_id=scope.conversation_id,
    task_id=scope.task_id,
    base_version_id=scope.base_version_id,
    target_version_id=scope.target_version_id,
    policy_version="hermes-lexical-v1",
    source_watermark_cursor=41,
    projection_generation=1,
    status=MemorySnapshotStatus.READY,
    items=(item,),
)
task.bind_memory_snapshot(snapshot.id, snapshot.content_hash)
assert task.memory_snapshot_id == snapshot.id
assert task.memory_snapshot_hash == snapshot.content_hash
with pytest.raises(InvalidTransitionError):
    task.bind_memory_snapshot(new_id(), "0" * 64)
assert bound_scope.scope_digest != unbound_scope.scope_digest
```

Also reject non-lowercase/non-hex hashes, negative cursors, duplicate ordinals, invalid score values, and a `READY` Snapshot whose projection watermark trails its source watermark.

- [ ] **Step 2: Run focused tests and confirm the missing-type failures**

```powershell
C:\Python313\Scripts\uv.exe run pytest tests/test_memory_retrieval_domain.py tests/test_domain.py tests/test_contracts.py -q
```

Expected before implementation: import failures for `fairy_core.memory.retrieval_models` and missing Task/Snapshot fields.

- [ ] **Step 3: Implement immutable retrieval value objects and canonical Snapshot hashing**

Canonical content hashing must serialize only reproducible manifest fields and ordered items:

```python
payload = {
    "snapshot_version": 1,
    "policy_version": policy_version,
    "source_watermark_cursor": source_watermark_cursor,
    "projection_generation": projection_generation,
    "status": status.value,
    "items": [item.canonical_payload() for item in items],
}
content_hash = hashlib.sha256(
    json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
```

Do not include random ID or creation time in the content hash. Freeze score-component mappings with `MappingProxyType` and reject NaN/Infinity.

- [ ] **Step 4: Add Task and Scope one-time bindings**

`Task.bind_memory_snapshot` accepts the first valid pair, permits an idempotent replay of the same pair, and raises `InvalidTransitionError` for any replacement. `ScopeContract.create` requires ID/hash to be both present or both absent and includes both in the digest payload.

- [ ] **Step 5: Add strict Pydantic contract models**

Expose UUIDs, lowercase 64-character hashes, ordered items, typed score components, total token count, status, and source watermark. Do not add a model-writable request field that can choose tenant, Project, Conversation, Version, policy version, or projection generation.

- [ ] **Step 6: Run Core formatting, lint, and tests; commit Task 10**

```powershell
C:\Python313\Scripts\uv.exe run ruff format src tests
C:\Python313\Scripts\uv.exe run ruff check src tests
C:\Python313\Scripts\uv.exe run pytest
git add fairy-v3/core
git commit -m "feat(v3): define immutable memory snapshots"
```

---

### Task 11: Persist Snapshots, Search Documents, Access Logs, and Projection Health

**Files:**
- Modify: `fairy-v3/core/src/fairy_core/memory/schema.py`
- Create: `fairy-v3/core/src/fairy_core/memory/snapshot_sqlalchemy.py`
- Modify: `fairy-v3/core/src/fairy_core/persistence/unit_of_work.py`
- Modify: `fairy-v3/core/src/fairy_core/persistence/sqlite.py`
- Modify: `fairy-v3/core/src/fairy_core/storage/schema.py`
- Modify: `fairy-v3/core/src/fairy_core/storage/sqlalchemy.py`
- Create: `fairy-v3/cloud/migrations/versions/20260711_0005_memory_retrieval.py`
- Modify: `fairy-v3/cloud/tests/test_deployment_contract.py`
- Modify: `fairy-v3/cloud/tests/test_postgres_contract.py`
- Test: `fairy-v3/core/tests/test_memory_snapshot_repository.py`
- Test: `fairy-v3/core/tests/test_state_store.py`
- Test: `fairy-v3/cloud/tests/integration/test_postgres_memory.py`

**Interfaces:**
- Produces: `SqlAlchemyMemorySnapshotRepository` bound to the caller's SQLAlchemy connection and tenant.
- Extends: `CoreUnitOfWork.snapshots: MemorySnapshotRepository`.
- Persists: `memory_snapshots`, `memory_snapshot_items`, `memory_search_documents`, `memory_access_log`, and `memory_projection_checkpoints`.
- Persists: `core_tasks.memory_snapshot_id` and `core_tasks.memory_snapshot_hash` without introducing a circular Task/Snapshot foreign key.

- [ ] **Step 1: Write red repository tests for atomic append/read, idempotent replay, tenant isolation, ordered items, and rollback**

```python
with factory() as uow:
    persisted = uow.snapshots.append(snapshot, request_fingerprint="a" * 64)
    uow.state.save_task(bound_task)
    uow.commit()
with factory() as uow:
    recovered = uow.snapshots.get_for_task(bound_task.id)
assert recovered == persisted
assert [item.ordinal for item in recovered.items] == list(range(len(recovered.items)))
```

Reusing a request fingerprint with different immutable content must raise `IdempotencyConflictError`.

- [ ] **Step 2: Extend runtime metadata and SQLite storage**

Use composite `(tenant_id, id)` ownership keys, unique `(tenant_id, task_id)` Snapshot binding, unique `(tenant_id, snapshot_id, ordinal)` item order, and scope indexes beginning with `tenant_id`. Store rendered source text and its SHA-256 so inspection remains reproducible; it is quoted source material, not hidden reasoning.

- [ ] **Step 3: Implement the connection-bound Snapshot repository**

Append the manifest and all items in one transaction, recompute content/token totals before insert, verify immutable fingerprints on replay, and reconstruct immutable dataclasses in ordinal order. Reads must require the current Task ID in addition to Snapshot ID where both are accepted.

- [ ] **Step 4: Add Alembic revision `20260711_0005`**

The migration must create all five retrieval tables, add Task binding columns, create PostgreSQL forced RLS policies, add composite foreign keys to canonical rows where they do not form a cycle, and fully reverse these changes on downgrade. The migration must not create pgvector, embeddings, Episodes, or generation-switch workers.

For `memory_search_documents`, create the PostgreSQL generated `search_vector` column and its GIN index in this revision so Task 12 can add the adapter without rewriting a migration that has already passed its own commit gate.

- [ ] **Step 5: Verify offline upgrade/downgrade DDL and real PostgreSQL when Docker exists**

```powershell
C:\Python313\Scripts\uv.exe run alembic upgrade head --sql > $env:TEMP\fairy-up.sql
C:\Python313\Scripts\uv.exe run alembic downgrade head:base --sql > $env:TEMP\fairy-down.sql
C:\Python313\Scripts\uv.exe run pytest tests/test_deployment_contract.py tests/test_postgres_contract.py -q
```

Expected: migration head `20260711_0005`, upgrade and downgrade SQL render successfully, and PostgreSQL integration remains explicitly skipped if Docker is unavailable.

- [ ] **Step 6: Run Core/Cloud verification and commit Task 11**

```powershell
C:\Python313\Scripts\uv.exe run pytest tests/test_memory_snapshot_repository.py tests/test_state_store.py -q
git add fairy-v3/core fairy-v3/cloud
git commit -m "feat(v3): persist memory snapshot provenance"
```

---

### Task 12: Implement SQLite FTS5 and PostgreSQL Lexical Projection Adapters

**Files:**
- Create: `fairy-v3/core/src/fairy_core/memory/search_sqlalchemy.py`
- Create: `fairy-v3/core/src/fairy_core/memory/search_queries.py`
- Create: `fairy-v3/core/src/fairy_core/memory/sqlite_fts.py`
- Modify: `fairy-v3/core/src/fairy_core/persistence/sqlite.py`
- Modify: `fairy-v3/core/src/fairy_core/persistence/unit_of_work.py`
- Create: `fairy-v3/cloud/migrations/versions/20260711_0006_fts_row_identity.py`
- Test: `fairy-v3/core/tests/test_memory_search.py`
- Test: `fairy-v3/core/tests/test_sqlite_core.py`
- Modify: `fairy-v3/cloud/tests/integration/test_postgres_memory.py`

**Interfaces:**
- Produces: `SqlAlchemyMemorySearchIndex` and `SqlAlchemyMemoryProjectionWriter`.
- Extends: `CoreUnitOfWork.memory_search` and `CoreUnitOfWork.memory_projections`.
- Produces: `search(scope, query, generation, limit) -> tuple[MemorySearchHit, ...]` with tenant/scope filtering applied before ranking.
- Produces: idempotent `upsert_documents`, `remove_source`, `get_health`, and `advance_checkpoint` operations.

- [ ] **Step 1: Write dialect-neutral contract tests and malicious-query tests**

Cover exact phrases, prefix/token queries, Unicode, punctuation-only input, quotes/operators, duplicate upserts, generation isolation, Conversation isolation, and cross-tenant identical IDs. Queries such as `" OR *` must be treated as text, not raw FTS syntax.

- [ ] **Step 2: Bootstrap SQLite FTS5 as an external-content table**

Create `memory_search_documents_fts` with `content='memory_search_documents'` and `content_rowid='fts_rowid'`, plus insert/update/delete synchronization triggers. `fts_rowid` is a stable, positive, globally unique projection identifier derived from tenant/source identity; do not use SQLite's implicit rowid because `VACUUM` may rewrite it. Verify FTS5 availability during local engine initialization; expose unavailable health instead of silently substituting an in-memory index.

- [ ] **Step 3: Implement parameterized SQLite search**

Tokenize and quote normalized user terms in Core code, use bound parameters for the FTS `MATCH` expression, join stable FTS row IDs to canonical projection rows, apply tenant/generation/scope predicates in SQL, and return deterministic ties ordered by authority, lexical score, source cursor, and source ID. Test that the index remains joined correctly after deleting an earlier row and running `VACUUM`.

- [ ] **Step 4: Implement PostgreSQL generated-vector search**

Use the generated column created by migration `0005`:

```sql
search_vector tsvector GENERATED ALWAYS AS (
  to_tsvector('simple', coalesce(normalized_text, ''))
) STORED
```

Add a GIN index on `search_vector` and B-tree scope/generation indexes. The adapter uses `websearch_to_tsquery('simple', :query)` with bound values and never concatenates user text into SQL.

- [ ] **Step 5: Verify both adapters and commit Task 12**

```powershell
C:\Python313\Scripts\uv.exe run pytest tests/test_memory_search.py tests/test_sqlite_core.py -q
C:\Python313\Scripts\uv.exe run ruff check src tests
git add fairy-v3/core fairy-v3/cloud
git commit -m "feat(v3): add scoped lexical memory search"
```

Real PostgreSQL assertions run only through the Docker integration profile; SQLite passing alone is not PostgreSQL verification.

---

### Task 13: Build Deterministic, Budgeted, Authority-aware Snapshots

**Files:**
- Create: `fairy-v3/core/src/fairy_core/memory/snapshot_builder.py`
- Modify: `fairy-v3/core/src/fairy_core/memory/policy.py`
- Modify: `fairy-v3/core/src/fairy_core/memory/sqlalchemy.py`
- Test: `fairy-v3/core/tests/test_memory_snapshot_builder.py`
- Test: `fairy-v3/core/tests/test_memory_properties.py`
- Test: `fairy-v3/core/tests/test_memory_policy.py`

**Interfaces:**
- Produces: `DeterministicMemorySnapshotBuilder` implementing `MemorySnapshotBuilder`.
- Produces: `Utf8ByteTokenCounter.count(text: str) -> int`; the byte count is a conservative deterministic ceiling independent of model tokenizer selection.
- Consumes: `MemoryRepository`, `MemorySearchIndex`, `MemorySnapshotRepository`, Scope IDs, query, source watermark, policy version, and projection health.

- [ ] **Step 1: Write red ranking, budget, conflict, expiry, fallback, and fixed-hash tests**

Assert this precedence:

```text
exact current project_canonical
current project_canonical
exact user_profile
current conversation_draft
lexical Observation/history
```

Add tests proving an exact canonical fact outranks a higher lexical score, conflicts render every live alternative with a visible conflict marker, expired revisions are excluded at the injected clock, blocked/secret content is excluded, and stale/unavailable projection produces `DEGRADED` with relational fallback.

- [ ] **Step 2: Implement deterministic candidate normalization and ranking**

Rank with explicit numeric components (`authority`, `exact`, `lexical`, `recency`, `confidence`) and stable tie breakers. Do not persist a free-form explanation. Render untrusted content through escaped, labeled source blocks and recompute every text hash.

- [ ] **Step 3: Implement section budgets and hard-ceiling behavior**

Select in section order, carry unused capacity only downward, and reserve exact current canonical facts before lower-authority material. If required canonical content alone exceeds 3,000 conservative tokens, raise `MemorySnapshotTooLargeError` instead of silently truncating it.

- [ ] **Step 4: Implement degraded relational fallback**

When search raises, reports unavailable, or has a checkpoint behind the requested memory watermark, select scoped current Claims plus a bounded recent Observation list. Persist `DEGRADED` and the projection health metadata used; never fabricate lexical scores.

- [ ] **Step 5: Add property tests for determinism and bounds**

For randomized candidate order, assert identical content hash and item order. Assert `token_count <= 3000`, contiguous ordinals, item hash integrity, finite scores, and no out-of-scope IDs.

- [ ] **Step 6: Verify and commit Task 13**

```powershell
C:\Python313\Scripts\uv.exe run pytest tests/test_memory_snapshot_builder.py tests/test_memory_properties.py tests/test_memory_policy.py -q
C:\Python313\Scripts\uv.exe run ruff check src tests
git add fairy-v3/core
git commit -m "feat(v3): build bounded immutable memory snapshots"
```

---

### Task 14: Refresh Lexical Projections and Bind One Snapshot per Task

**Files:**
- Create: `fairy-v3/core/src/fairy_core/memory/projection.py`
- Modify: `fairy-v3/core/src/fairy_core/memory/application.py`
- Modify: `fairy-v3/core/src/fairy_core/application/core.py`
- Modify: `fairy-v3/core/src/fairy_core/commanding/registry.py`
- Modify: `fairy-v3/core/src/fairy_core/commanding/ports.py`
- Modify: `fairy-v3/core/src/fairy_core/commanding/sqlalchemy.py`
- Test: `fairy-v3/core/tests/test_memory_projection.py`
- Test: `fairy-v3/core/tests/test_memory_application.py`
- Test: `fairy-v3/core/tests/test_core_application.py`
- Test: `fairy-v3/core/tests/test_persistence_recovery.py`

**Interfaces:**
- Produces: `LexicalProjectionRefresher.refresh(generation: int = 1) -> MemoryProjectionHealth`.
- Adds internal registered tools: `memory.projection.refresh` and `memory.snapshot.build`; neither is agent-exposed.
- Extends: `CommandLedger.current_cursor() -> int` for constant-size watermark reads.
- Guarantees: every successfully created Task is returned with a bound Snapshot ID/hash in both Task and Scope contracts.

- [ ] **Step 1: Write red lifecycle and crash-point tests**

Cover projection refresh after Observation/Claim commits, refresh failure after canonical commit, Task crash before Snapshot binding, idempotent Task replay, a write after binding that does not alter the old Snapshot, and a workspace command whose Scope digest includes the bound Snapshot pair.

- [ ] **Step 2: Implement canonical-to-projection refresh**

Read current scoped Claims/revisions and accepted searchable Observations, create documents with source hashes/cursors, upsert generation 1 idempotently, remove tombstoned/expired source rows, then advance the checkpoint only after all rows succeed. The first implementation does not create Episodes, embeddings, parallel generations, or `SKIP LOCKED` rebuild workers.

- [ ] **Step 3: Run refresh only after canonical memory commits**

Memory mutation commands commit their canonical transaction first. A second durable `memory.projection.refresh` command performs projection work; failure marks that command failed and leaves the successful canonical response intact so later Snapshot building uses degraded fallback.

- [ ] **Step 4: Bind a Snapshot during Task scope resolution**

Create and start `memory.snapshot.build`, build/persist the Snapshot in the same database transaction as Task binding, complete the command with Snapshot ID/hash, then construct the workspace command Scope from the bound pair. A Task replay with the same idempotency key reuses the same pair; a different pair is rejected.

- [ ] **Step 5: Prove bounded-memory semantics**

After Task binding, promote a new Claim and assert the old Task's Snapshot items/hash are unchanged. A newly created Task receives a new Snapshot whose watermark may include the Claim.

- [ ] **Step 6: Verify and commit Task 14**

```powershell
C:\Python313\Scripts\uv.exe run pytest tests/test_memory_projection.py tests/test_memory_application.py tests/test_core_application.py tests/test_persistence_recovery.py -q
C:\Python313\Scripts\uv.exe run pytest
git add fairy-v3/core
git commit -m "feat(v3): bind memory snapshots to tasks"
```

---

### Task 15: Expose Search, Snapshot Inspection, and Projection Health Contracts

**Files:**
- Modify: `fairy-v3/core/src/fairy_core/contracts/models.py`
- Modify: `fairy-v3/core/src/fairy_core/contracts/methods.py`
- Modify: `fairy-v3/core/src/fairy_core/application/service.py`
- Modify: `fairy-v3/cloud/src/fairy_cloud/api.py`
- Modify: `fairy-v3/desktop/src/core/client.ts`
- Modify: `fairy-v3/desktop/src/core/contracts.ts`
- Modify: `fairy-v3/desktop/src/core/memoryValidation.ts`
- Regenerate: `fairy-v3/desktop/src/core/generated/api.d.ts`
- Test: `fairy-v3/core/tests/test_memory_contracts.py`
- Test: `fairy-v3/core/tests/test_jsonrpc_transport.py`
- Test: `fairy-v3/cloud/tests/test_http_contract.py`
- Test: `fairy-v3/desktop/src/core/client.test.ts`
- Test: `fairy-v3/desktop/src/core/cloudTransport.test.ts`

**Interfaces:**
- Adds: `memory.search`, `memory.snapshots.get`, and `memory.projection.health` to `CORE_METHODS`.
- Adds: `CoreClient.memory.search`, `CoreClient.memory.snapshots.get`, and `CoreClient.memory.projection.health`.
- Requires: every request carries `task_id`; Core resolves all tenant/Project/Conversation/Version/generation fields.

- [ ] **Step 1: Write REST/JSON-RPC/CoreClient parity tests before changing contracts**

Validate strict unknown-field rejection, scoped Task ownership, `limit` in `1..100`, non-empty query up to 10,000 characters, Snapshot item order, finite score components, lowercase hashes, and explicit projection state/degraded reason.

- [ ] **Step 2: Add transport-independent Core methods**

Use these method names exactly:

```text
memory.search
memory.snapshots.get
memory.projection.health
```

Snapshot inspection returns only the Task-bound Snapshot. Search filters with the resolved Task Scope before returning any hit. Projection diagnostics expose generation, watermarks, state, lag, and last public error code, not stack traces or reasoning.

- [ ] **Step 3: Add REST routes and generated TypeScript contracts**

Use `GET /memory/search`, `GET /memory/snapshots/{snapshot_id}`, and `GET /memory/projection/health` with `operation_id` values matching Core method names. Regenerate OpenAPI-derived declarations and reject hand-edited drift.

- [ ] **Step 4: Add runtime Zod validation and CoreClient groups**

Keep memory schemas in `memoryValidation.ts`; validate cloud payloads before returning them. Do not move retrieval schema ownership back into `cloudTransport.ts`.

- [ ] **Step 5: Run contract verification and commit Task 15**

```powershell
C:\Python313\Scripts\uv.exe run pytest tests/test_memory_contracts.py tests/test_jsonrpc_transport.py -q
C:\Python313\Scripts\uv.exe run pytest tests/test_http_contract.py -q
npm test -- --run
npm run build
git add fairy-v3/core fairy-v3/cloud fairy-v3/desktop
git commit -m "feat(v3): expose memory snapshot retrieval"
```

---

### Task 16: Gate Recovery, Security, Contracts, Migrations, and Real Database Behavior

**Files:**
- Create: `fairy-v3/core/tests/test_memory_retrieval_security.py`
- Create: `fairy-v3/core/tests/test_memory_retrieval_recovery.py`
- Create: `fairy-v3/cloud/tests/integration/test_postgres_memory_retrieval.py`
- Modify: `fairy-v3/cloud/tests/integration/test_postgres_tenant_rls.py`
- Modify: `fairy-v3/scripts/test-all.ps1`
- Modify: `fairy-v3/docs/threat-model.md`

**Interfaces:**
- Produces: one reproducible whole-repository verification entry point with optional Docker integration.
- Verifies: SQLite/PostgreSQL contract parity, FTS query safety, scope isolation, crash recovery, deterministic hashes, and migration reversibility.

- [ ] **Step 1: Add adversarial security and recovery tests**

Cover FTS operator injection, Unicode bidi/control characters, malicious synced text, tenant/Conversation/Version leakage, forged Scope IDs, stale projection fences, crash before checkpoint, crash after document upsert, crash after Snapshot insert, and idempotent replay.

- [ ] **Step 2: Add real PostgreSQL/RLS integration tests**

Use the existing Docker integration fixture to prove generated `tsvector` search, GIN-backed query execution, forced RLS, same IDs in two tenants, rollback behavior, and Snapshot item ordering. Do not substitute mocked dialect compilation.

- [ ] **Step 3: Extend the full verification script**

Keep lock checks, Ruff, Core/Cloud suites, Rust checks, desktop tests/build, contract generation diff, repository boundaries, and offline migration upgrade/downgrade. Add retrieval integration under the existing Docker-availability branch and print an explicit skip when Docker cannot be reached.

- [ ] **Step 4: Run the complete gate and commit Task 16**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test-all.ps1
git add fairy-v3/core/tests fairy-v3/cloud/tests fairy-v3/scripts fairy-v3/docs/threat-model.md
git commit -m "test(v3): gate memory snapshot retrieval"
```

Expected without Docker: every local/static gate passes and PostgreSQL/S3 tests are reported as skipped. Expected with Docker: PostgreSQL 18.4 and S3 integration suites pass against live containers.

---

### Task 17: Close the Lexical Snapshot Milestone and Clean Structure

**Files:**
- Modify: `fairy-v3/README.md`
- Modify: `fairy-v3/core/README.md`
- Modify: `fairy-v3/cloud/README.md`
- Modify: `fairy-v3/docs/architecture.md`
- Create: `fairy-v3/docs/adr/0004-immutable-memory-snapshots.md`
- Modify: `fairy-v3/docs/superpowers/specs/2026-07-10-hermes-database-memory-design.md`
- Modify: package `__init__.py` files only where a public export is required

**Interfaces:**
- Documents: canonical versus projection ownership, Task-bound Snapshot lifecycle, deterministic budgets, degradation behavior, and local/cloud adapter parity.
- Records: Episodes, pgvector, semantic expansion, outbox rebuild workers, parallel generation switching, multi-device tombstone propagation, and performance tuning remain in later approved slices.

- [ ] **Step 1: Review the complete branch for boundary and naming drift**

Search for duplicate Snapshot/Search types, dialect checks outside adapters, raw user FTS syntax, direct transport-to-database calls, Task Snapshot replacement paths, stale migration heads, broad package re-exports, and files that now mix domain, SQL, transport, and rendering responsibilities.

- [ ] **Step 2: Apply only milestone-local cleanup**

Split files that exceed one clear responsibility, remove compatibility aliases introduced only during this slice, keep external public contract names stable, and leave unrelated legacy/root cleanup untouched.

- [ ] **Step 3: Update architecture and ADR**

Describe exact authority ordering, snapshot hash inputs, projection checkpoint semantics, error/degraded behavior, and why projection freshness is not allowed to mutate an already-bound Task context.

- [ ] **Step 4: Run the final gate from a clean worktree and commit Task 17**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test-all.ps1
git status --short
git add fairy-v3
git commit -m "chore(v3): close lexical memory snapshot milestone"
```

Expected: only the Task 17 documentation/cleanup delta is committed, generated contracts are clean, and the worktree is clean after commit.

## Self-review Record

- **Spec coverage:** Slice 2 requirements map to Tasks 10-16: full-text search (12), immutable Snapshot/provenance (10-11), deterministic budgets/ranking (13), Task binding (14), public inspection/health (15), and recovery/security/database parity (16).
- **Explicit deferrals:** Episodes, pgvector, embedding storage, semantic expansion, `SKIP LOCKED` projection rebuild workers, and parallel generation switching remain Slice 3. Multi-device conflict/tombstone sync, user controls, and performance tuning remain Slice 4.
- **Type consistency:** `MemorySnapshotStatus`, `ProjectionState`, Snapshot ID/hash fields, generation, watermark, and score-component names are defined once in Task 10 and consumed unchanged by later tasks.
- **Boundary check:** Canonical rows are written through repositories in the shared Unit of Work; dialect-specific FTS remains inside `search_sqlalchemy.py`/`sqlite_fts.py`; transports call `CoreService` only.
- **Placeholder scan:** No `TBD`, `TODO`, implicit “similar to” step, or unspecified error-handling step remains in this plan.
