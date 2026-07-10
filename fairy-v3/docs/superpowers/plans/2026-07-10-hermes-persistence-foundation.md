# Hermes Persistence Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Fairy Cloud's process-local SQLite composition with one tenant-scoped transactional persistence authority and add the canonical Hermes Claim/Observation database layer on top of it.

**Architecture:** SQLite and PostgreSQL implement the same synchronous StateStore, CommandLedger, MemoryRepository, and CoreUnitOfWork ports. One canonical Project row owns revision/Active Version state, one canonical Event Ledger feeds SSE and outbox delivery, and Hermes memory stores versioned relational evidence and claims while FTS/vector remain rebuildable projections outside this first delivery slice.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0.51+, SQLite, PostgreSQL 18.4, psycopg 3, Alembic 1.18+, FastAPI 0.139.x, Pydantic 2.13+, pytest 9.1+, Hypothesis 6.156+, Docker Compose.

## Global Constraints

- V3 cannot import legacy root `app/`, `fairy-desktop/`, `skills/`, `lazy_runtime/`, or `legacy_surface/` modules.
- Project-first, Task-driven, Preview-first; a Conversation is not project state.
- Every side effect crosses the Command Bus and is appended durably before execution.
- Every database-owned row is tenant-scoped; PostgreSQL also enforces forced row level security.
- `(tenant_id, id)` is the ownership key; idempotency and task-sequence constraints include `tenant_id`.
- Production PostgreSQL schemas are changed only through Alembic; `create_all` is SQLite-only.
- One canonical Project row owns `revision` and `active_version_id`.
- One canonical Event Ledger owns the global cursor used by Core, sync, SSE, and outbox.
- A reused idempotency key with a different request fingerprint returns `IDEMPOTENCY_CONFLICT`.
- No transaction remains open across filesystem, network, sandbox, model, or object-store I/O.
- Vector/FTS data is a rebuildable projection and cannot create or overwrite a canonical memory Claim.
- Project Canonical Memory outranks personal preference; Conversation Draft Memory cannot leak across Conversations.
- Companion never calls an LLM, owns project state, or reads/writes Hermes memory.
- No hidden reasoning or chain-of-thought is persisted as a memory payload or user-visible event.
- Cloud remains brokerless: PostgreSQL lease/outbox, no Redis, no NATS, no Docker socket.
- The current worktree contains interrupted Task 1 changes. Preserve and finish them; do not reset or recreate them.

---

### Task 1: Finish Tenant-aware State and Command Ledger Adapters

**Files:**
- Modify: `fairy-v3/core/src/fairy_core/domain/errors.py`
- Modify: `fairy-v3/core/src/fairy_core/storage/schema.py`
- Modify: `fairy-v3/core/src/fairy_core/storage/sqlalchemy.py`
- Modify: `fairy-v3/core/src/fairy_core/storage/sqlite.py`
- Create: `fairy-v3/core/src/fairy_core/storage/sqlite_migrations.py`
- Create: `fairy-v3/core/src/fairy_core/commanding/models.py`
- Create: `fairy-v3/core/src/fairy_core/commanding/ports.py`
- Create: `fairy-v3/core/src/fairy_core/commanding/schema.py`
- Create: `fairy-v3/core/src/fairy_core/commanding/sqlalchemy.py`
- Create: `fairy-v3/core/src/fairy_core/commanding/sqlite.py`
- Create: `fairy-v3/core/src/fairy_core/commanding/sqlite_migrations.py`
- Modify: `fairy-v3/core/src/fairy_core/commanding/ledger.py`
- Modify: `fairy-v3/core/src/fairy_core/commanding/__init__.py`
- Modify: `fairy-v3/core/src/fairy_core/commanding/bus.py`
- Modify: `fairy-v3/core/src/fairy_core/transports/jsonrpc.py`
- Modify: `fairy-v3/core/src/fairy_core/transports/stdio.py`
- Test: `fairy-v3/core/tests/test_state_store.py`
- Test: `fairy-v3/core/tests/test_ledger.py`

**Interfaces:**
- Produces: `StateStore`, `SqlAlchemyStateStore`, `SqliteStateStore`.
- Produces: `CommandLedger`, `SqlAlchemyCommandLedger`, `SqliteCommandLedger`.
- Produces: `CommandRun.lease_fence: int` and stable `IdempotencyConflictError.code = "IDEMPOTENCY_CONFLICT"`.
- Preserves: `fairy_core.commanding.ledger` as a compatibility re-export module.

- [ ] **Step 1: Complete the red tests already added for SQLite migration, UTC normalization, tenant isolation, fingerprint conflicts, and expired lease reclamation**

Keep these assertions in the focused suites:

```python
assert recovered.created_at == datetime(2026, 1, 2, 0, tzinfo=UTC)
assert tenant_b.get_project(project_a.id) is None
with pytest.raises(IdempotencyConflictError):
    ledger.create_run(
        command_name="project.read",
        actor="agent",
        scope=scope,
        input_payload={"query": "different"},
        risk_level=RiskLevel.LOW,
        idempotency_key="same-key",
    )
assert second_claim.lease_fence == first_claim.lease_fence + 1
```

- [ ] **Step 2: Run the focused tests and confirm failures describe unfinished adapters**

Run:

```powershell
C:\Python313\Scripts\uv.exe run pytest tests/test_state_store.py tests/test_ledger.py -q
```

Expected before completion: import or behavior failures for the new command adapter; state tests already fixed during the interrupted TDD cycle remain green.

- [ ] **Step 3: Finish `SqlAlchemyCommandLedger` with atomic request fingerprinting, CAS transitions, atomic task sequences, and fenced lease reclaim**

The public port must be concrete enough for CommandBus without referring to SQLite:

```python
class CommandLedger(Protocol):
    def create_run(
        self,
        *,
        command_name: str,
        actor: str,
        scope: ScopeContract,
        input_payload: dict[str, Any],
        risk_level: RiskLevel,
        idempotency_key: str,
    ) -> CommandRun:
        raise NotImplementedError

    def transition(self, run_id: UUID, status: CommandStatus) -> CommandRun:
        raise NotImplementedError

    def claim_next(self, *, worker_id: str, lease_until: datetime) -> CommandRun | None:
        raise NotImplementedError
```

Use a dialect-specific conflict-safe insert with `RETURNING`, compare the stored SHA-256 request fingerprint on replay, allocate task sequence with an upsert counter, and use `FOR UPDATE SKIP LOCKED` only for PostgreSQL.

- [ ] **Step 4: Add an idempotent pre-tenant V3 SQLite ledger importer**

Copy old `command_runs` and `command_events` into tenant-aware tables under tenant `local`, preserve cursors and task sequence, compute request fingerprints from the stored scope/input, and record a local migration revision. Do not read any legacy Fairy application database.

- [ ] **Step 5: Replace concrete ledger type annotations with `CommandLedger` and retain compatibility imports**

`CommandBus` and transport constructors import models and the port from `fairy_core.commanding`, while `commanding/ledger.py` contains only explicit re-exports:

```python
from fairy_core.commanding.models import CommandRun, CommandStatus, EventEnvelope, EventVisibility
from fairy_core.commanding.sqlite import SqliteCommandLedger

__all__ = ["CommandRun", "CommandStatus", "EventEnvelope", "EventVisibility", "SqliteCommandLedger"]
```

- [ ] **Step 6: Run focused and complete Core verification**

Run:

```powershell
C:\Python313\Scripts\uv.exe run ruff format src tests
C:\Python313\Scripts\uv.exe run ruff check src tests
C:\Python313\Scripts\uv.exe run pytest
```

Expected: all Core tests pass; no raw `sqlite3` implementation remains in `commanding/ledger.py`.

- [ ] **Step 7: Commit Task 1**

```powershell
git add fairy-v3/core/src/fairy_core fairy-v3/core/tests/test_state_store.py fairy-v3/core/tests/test_ledger.py
git commit -m "refactor(v3): make command persistence tenant aware"
```

---

### Task 2: Add a Shared Transactional Core Unit of Work

**Files:**
- Create: `fairy-v3/core/src/fairy_core/persistence/__init__.py`
- Create: `fairy-v3/core/src/fairy_core/persistence/session.py`
- Create: `fairy-v3/core/src/fairy_core/persistence/unit_of_work.py`
- Modify: `fairy-v3/core/src/fairy_core/storage/sqlalchemy.py`
- Modify: `fairy-v3/core/src/fairy_core/commanding/sqlalchemy.py`
- Modify: `fairy-v3/core/src/fairy_core/application/core.py`
- Modify: `fairy-v3/core/src/fairy_core/commanding/bus.py`
- Modify: `fairy-v3/core/src/fairy_core/transports/stdio.py`
- Test: `fairy-v3/core/tests/test_unit_of_work.py`
- Test: `fairy-v3/core/tests/test_core_application.py`

**Interfaces:**
- Produces: `CoreUnitOfWorkFactory.__call__() -> CoreUnitOfWork`.
- Produces: `CoreUnitOfWork.state: StateStore` and `CoreUnitOfWork.commands: CommandLedger` bound to one SQLAlchemy `Connection`.
- Produces: `commit()` and rollback-on-exit semantics.

- [ ] **Step 1: Write rollback and shared-connection tests**

```python
def test_unit_of_work_rolls_back_state_and_command_event_together(engine):
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    with pytest.raises(RuntimeError):
        with factory() as uow:
            uow.state.save_project(project)
            uow.commands.create_run(
                command_name="project.read",
                actor="agent",
                scope=scope,
                input_payload={"query": "entrypoints"},
                risk_level=RiskLevel.LOW,
                idempotency_key="rollback-command",
            )
            raise RuntimeError("crash")
    with factory() as uow:
        assert uow.state.get_project(project.id) is None
        assert uow.commands.events_after(cursor=0) == []
```

Add a commit test proving both records become visible together.

- [ ] **Step 2: Run the new test and confirm it fails because no Unit of Work exists**

Run:

```powershell
C:\Python313\Scripts\uv.exe run pytest tests/test_unit_of_work.py -q
```

Expected: import failure for `SqlAlchemyUnitOfWorkFactory`.

- [ ] **Step 3: Implement connection-bound repository sessions**

`SqlAlchemySession` accepts `Engine | Connection`; `read()` reuses a bound connection and `write()` never starts a nested transaction when one is already owned by the Unit of Work.

```python
@contextmanager
def write(self) -> Iterator[Connection]:
    if isinstance(self._bind, Connection):
        yield self._bind
    else:
        with self._bind.begin() as connection:
            yield connection
```

- [ ] **Step 4: Implement `SqlAlchemyUnitOfWorkFactory`**

```python
class CoreUnitOfWork(Protocol):
    state: StateStore
    commands: CommandLedger
    def commit(self) -> None:
        raise NotImplementedError

class SqlAlchemyUnitOfWorkFactory:
    def __init__(self, engine: Engine, *, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    def __call__(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self._engine, tenant_id=self._tenant_id)
```

On PostgreSQL entry, run `SET LOCAL app.tenant_id = :tenant_id`. Roll back unless `commit()` completed. Dispose only caller-owned engines.

- [ ] **Step 5: Refactor Core use cases into short database phases around external operations**

For workspace operations, use this sequence:

```text
UoW A: create state + durable command intent + commit
outside DB: execute idempotent workspace operation
UoW B: verify lease/fence + persist result + terminal event + commit
```

Do not hold UoW A or B open during `WorkspaceProvisioner` calls.

- [ ] **Step 6: Add crash-point tests around Task creation and Changeset approval**

Test a crash after intent commit and before workspace execution, then replay the same idempotency key. Assert no orphan Version, no duplicate command, and an explicitly recoverable or interrupted Task state.

- [ ] **Step 7: Run Core verification and commit**

```powershell
C:\Python313\Scripts\uv.exe run pytest
git add fairy-v3/core
git commit -m "feat(v3): add transactional core unit of work"
```

---

### Task 3: Create the Canonical PostgreSQL Tenant, Project, Event, and Outbox Schema

**Files:**
- Modify: `fairy-v3/cloud/migrations/env.py`
- Create: `fairy-v3/cloud/migrations/versions/20260710_0002_canonical_core.py`
- Modify: `fairy-v3/cloud/src/fairy_cloud/storage/schema.py`
- Modify: `fairy-v3/cloud/src/fairy_cloud/storage/postgres.py`
- Modify: `fairy-v3/cloud/tests/test_deployment_contract.py`
- Modify: `fairy-v3/cloud/tests/test_postgres_contract.py`
- Modify: `fairy-v3/cloud/tests/integration/test_postgres_sync.py`
- Create: `fairy-v3/cloud/tests/integration/test_postgres_tenant_rls.py`

**Interfaces:**
- Produces: one `core_projects` revision authority.
- Produces: one tenant-aware `domain_events` table with global cursor.
- Produces: tenant-aware fenced `outbox`, `version_candidates`, and `worker_leases`.
- Removes: runtime dependency on `cloud_projects` after data migration.

- [ ] **Step 1: Change migration-head and schema parity tests to expect revision `20260710_0002`**

```python
assert scripts.get_heads() == ["20260710_0002"]
assert scripts.get_revision("20260710_0002").down_revision == "20260710_0001"
```

Add offline DDL assertions for `core_projects`, `command_runs`, `task_event_sequences`, tenant columns, RLS policies, and outbox fence.

- [ ] **Step 2: Run deployment tests and confirm the missing revision failure**

```powershell
C:\Python313\Scripts\uv.exe run pytest tests/test_deployment_contract.py tests/test_postgres_contract.py -q
```

Expected: migration head mismatch and missing canonical tables.

- [ ] **Step 3: Implement migration `20260710_0002`**

The migration must:

1. Create Core state and command tables with composite tenant keys and foreign keys.
2. Add `tenant_id`, `run_id`, and `message` to `domain_events`.
3. Backfill an opaque SHA-256 tenant ID for each existing `user_id` in Python migration code.
4. Backfill `core_projects` from `cloud_projects`, preserving revision and Active Version.
5. Add tenant IDs and fence columns to outbox, candidates, and leases.
6. Replace global idempotency/event uniqueness with tenant-scoped constraints.
7. Enable and force RLS on every tenant-owned table.
8. Create policies using `current_setting('app.tenant_id', true)`.
9. Drop `cloud_projects` only after count and ownership checks pass.

- [ ] **Step 4: Make runtime metadata match migration DDL**

Alembic `target_metadata` receives the state, command, and cloud metadata collections. Add a schema drift test that compares expected table and constraint names instead of calling `create_all`.

- [ ] **Step 5: Make event/outbox replay verify payload fingerprints**

When an existing tenant/event ID is found, compare all immutable envelope fields and the canonical payload SHA-256. Raise `IDEMPOTENCY_CONFLICT` on mismatch instead of reusing the old cursor.

- [ ] **Step 6: Add real PostgreSQL RLS, dual-connection CAS, and stale-fence tests**

Use two connections with different `SET LOCAL app.tenant_id` values. Assert cross-tenant SELECT, UPDATE, and event replay return no rows even when IDs are identical.

- [ ] **Step 7: Verify offline migration and commit**

```powershell
C:\Python313\Scripts\uv.exe run alembic upgrade head --sql
C:\Python313\Scripts\uv.exe run pytest -m "not integration"
git add fairy-v3/cloud fairy-v3/core/src/fairy_core/storage/schema.py fairy-v3/core/src/fairy_core/commanding/schema.py
git commit -m "feat(v3): establish canonical cloud persistence"
```

If Docker is unavailable, integration tests must be collected and skipped with the existing explicit marker; report that limitation without claiming PostgreSQL execution passed.

---

### Task 4: Replace Transport-on-Transport Cloud Composition with CoreService

**Files:**
- Create: `fairy-v3/core/src/fairy_core/application/service.py`
- Create: `fairy-v3/core/src/fairy_core/contracts/methods.py`
- Modify: `fairy-v3/core/src/fairy_core/transports/jsonrpc.py`
- Modify: `fairy-v3/core/src/fairy_core/transports/stdio.py`
- Modify: `fairy-v3/cloud/src/fairy_cloud/api.py`
- Modify: `fairy-v3/cloud/src/fairy_cloud/dispatchers.py`
- Modify: `fairy-v3/cloud/src/fairy_cloud/main.py`
- Modify: `fairy-v3/cloud/src/fairy_cloud/settings.py`
- Modify: `fairy-v3/cloud/pyproject.toml`
- Test: `fairy-v3/core/tests/test_core_service.py`
- Test: `fairy-v3/core/tests/test_jsonrpc_transport.py`
- Test: `fairy-v3/cloud/tests/test_dispatchers.py`
- Test: `fairy-v3/cloud/tests/test_http_contract.py`

**Interfaces:**
- Produces: `CoreService.invoke(method: str, params: Mapping[str, Any]) -> Any` with membership checked against `CORE_METHODS`.
- Produces: generated `CORE_METHODS` catalog consumed by JSON-RPC, FastAPI, and contract tests.
- Produces: tenant-bound PostgreSQL Core runtime from `TenantRuntimeRegistry`.

- [ ] **Step 1: Write parity tests proving JSON-RPC and FastAPI call one fake CoreService directly**

Assert FastAPI does not construct a JSON-RPC envelope and `TenantRuntimeRegistry` never imports or calls `build_local_dispatcher`.

- [ ] **Step 2: Run tests and confirm current transport nesting fails the assertions**

```powershell
C:\Python313\Scripts\uv.exe run pytest tests/test_dispatchers.py tests/test_http_contract.py -q
```

- [ ] **Step 3: Extract the stable method catalog and application service**

```python
@dataclass(frozen=True, slots=True)
class CoreMethod:
    name: str
    request_model: type[BaseModel]
    response_model: type[BaseModel]

CORE_METHODS: Mapping[str, CoreMethod] = {
    "projects.get": CoreMethod(
        name="projects.get",
        request_model=ProjectIdInput,
        response_model=ProjectModel,
    ),
    "tasks.get": CoreMethod(
        name="tasks.get",
        request_model=TaskIdInput,
        response_model=TaskModel,
    ),
}
```

Move handler ownership out of `JsonRpcDispatcher`; JSON-RPC becomes error-envelope parsing around `CoreService.invoke`.

- [ ] **Step 4: Build tenant PostgreSQL runtime composition**

Add `psycopg[binary]` to Cloud only. Convert the configured async DSN to an explicit `postgresql+psycopg` DSN for synchronous Core, create one pooled engine, and construct tenant-bound UoW factories plus tenant workspace roots. `core_data_dir` stores workspace files only, never SQLite state.

- [ ] **Step 5: Run synchronous Core calls from FastAPI's threadpool in async routes**

Use `run_in_threadpool(service.invoke, method, params)` for async route handlers and SSE fallback work. Sync handlers may call the service directly because FastAPI already runs them in its worker pool.

- [ ] **Step 6: Add production-composition SSE test**

Create a command through the Cloud route, then subscribe through configured PostgreSQL event storage and assert the command event appears once with its global cursor.

- [ ] **Step 7: Regenerate contracts, run Core/Cloud/Desktop contract tests, and commit**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\generate-contracts.ps1
C:\Python313\Scripts\uv.exe run pytest
git add fairy-v3/core fairy-v3/cloud fairy-v3/contracts fairy-v3/desktop/src/core/generated
git commit -m "refactor(v3): share core service across transports"
```

---

### Task 5: Implement Hermes Memory Domain and Policy

**Files:**
- Create: `fairy-v3/core/src/fairy_core/memory/__init__.py`
- Create: `fairy-v3/core/src/fairy_core/memory/models.py`
- Create: `fairy-v3/core/src/fairy_core/memory/policy.py`
- Create: `fairy-v3/core/src/fairy_core/memory/ports.py`
- Modify: `fairy-v3/core/src/fairy_core/contracts/models.py`
- Modify: `fairy-v3/core/src/fairy_core/domain/errors.py`
- Test: `fairy-v3/core/tests/test_memory_domain.py`
- Test: `fairy-v3/core/tests/test_memory_policy.py`

**Interfaces:**
- Produces: `MemoryNamespace`, `MemoryAuthority`, `ObservationStatus`, `ClaimStatus`.
- Produces: immutable `MemoryObservation`, `MemoryClaim`, `MemoryClaimRevision`, and `MemoryTombstone`.
- Produces: complete `CLAIM_TRANSITIONS: Mapping[ClaimStatus, frozenset[ClaimStatus]]` and the test helper `claim_in_status` in the test module.
- Produces: `MemoryPolicy.evaluate_promotion(observation, target_namespace, scope) -> MemoryPolicyDecision`.

- [ ] **Step 1: Write state-machine and property tests first**

Cover candidate to active/conflicted/superseded/expired/forgotten transitions, monotonically increasing revisions, immutable provenance, and rejection of cross-Scope promotion.

```python
@given(st.sampled_from(tuple(ClaimStatus)), st.sampled_from(tuple(ClaimStatus)))
def test_claim_state_machine_matches_declared_transitions(current, target):
    claim = claim_in_status(current)
    if target in CLAIM_TRANSITIONS[current]:
        claim.transition_to(target)
        assert claim.status is target
    else:
        with pytest.raises(InvalidTransitionError):
            claim.transition_to(target)
```

- [ ] **Step 2: Run tests and confirm missing-domain imports**

```powershell
C:\Python313\Scripts\uv.exe run pytest tests/test_memory_domain.py tests/test_memory_policy.py -q
```

- [ ] **Step 3: Implement exact enums and immutable domain records**

```python
class MemoryNamespace(StrEnum):
    PROJECT_CANONICAL = "project_canonical"
    CONVERSATION_DRAFT = "conversation_draft"
    USER_PROFILE = "user_profile"
    DEVICE_LOCAL = "device_local"
    TASK_EPISODE = "task_episode"
```

Claim revisions carry typed JSON value, normalized text, source Observation IDs, source event IDs, authority, confidence, valid-time interval, actor, and superseded revision.

- [ ] **Step 4: Implement policy precedence and scanning gates**

Reject model-supplied identity, secret-like values, invisible Unicode controls, instruction-like memory payloads, and every namespace/Scope mismatch. Require explicit user approval to promote a model suggestion into Project Canonical Memory.

- [ ] **Step 5: Add stable errors**

Add the exact codes from the design spec: `MEMORY_SCOPE_VIOLATION`, `MEMORY_CONFLICT`, `MEMORY_INJECTION_BLOCKED`, `MEMORY_SECRET_BLOCKED`, `MEMORY_PROJECTION_STALE`, `MEMORY_SNAPSHOT_TOO_LARGE`, and `MEMORY_FORGOTTEN`.

- [ ] **Step 6: Run tests and commit**

```powershell
C:\Python313\Scripts\uv.exe run pytest tests/test_memory_domain.py tests/test_memory_policy.py -q
git add fairy-v3/core/src/fairy_core/memory fairy-v3/core/src/fairy_core/domain/errors.py fairy-v3/core/src/fairy_core/contracts/models.py fairy-v3/core/tests/test_memory_domain.py fairy-v3/core/tests/test_memory_policy.py
git commit -m "feat(v3): define Hermes memory domain"
```

---

### Task 6: Persist Hermes Observations, Claims, Revisions, and Tombstones

**Files:**
- Create: `fairy-v3/core/src/fairy_core/memory/schema.py`
- Create: `fairy-v3/core/src/fairy_core/memory/sqlalchemy.py`
- Modify: `fairy-v3/core/src/fairy_core/memory/ports.py`
- Modify: `fairy-v3/core/src/fairy_core/persistence/unit_of_work.py`
- Create: `fairy-v3/cloud/migrations/versions/20260710_0003_hermes_memory.py`
- Modify: `fairy-v3/cloud/migrations/env.py`
- Test: `fairy-v3/core/tests/test_memory_repository.py`
- Test: `fairy-v3/core/tests/test_memory_unit_of_work.py`
- Create: `fairy-v3/cloud/tests/integration/test_postgres_memory.py`

**Interfaces:**
- Produces: `MemoryRepository.append_observation`, `create_claim`, `append_revision`, `resolve_conflict`, `forget`, and scoped reads.
- Extends: `CoreUnitOfWork.memory: MemoryRepository`.

- [ ] **Step 1: Write one repository contract suite parameterized by adapter factory**

The suite must cover identical IDs across tenants, duplicate source event replay, revision CAS, conflict-set retention, expiry, tombstones, and rollback with state/command/event writes.

- [ ] **Step 2: Run SQLite contract tests and confirm missing repository implementation**

```powershell
C:\Python313\Scripts\uv.exe run pytest tests/test_memory_repository.py tests/test_memory_unit_of_work.py -q
```

- [ ] **Step 3: Define relational tables**

Create `memory_observations`, `memory_claims`, `memory_claim_revisions`, and `memory_tombstones` with `(tenant_id, id)` composite keys and Scope foreign keys. Claim revision uniqueness is `(tenant_id, claim_id, revision)` and one partial unique index identifies the current revision.

- [ ] **Step 4: Implement atomic repository methods on the bound Unit of Work connection**

Use request/content fingerprints on idempotent inserts. Append revisions with a compare-and-swap update guarded by `current_revision = expected_revision` and return the new revision; on zero rows, load current state and raise `MEMORY_CONFLICT`.

- [ ] **Step 5: Add Alembic revision `20260710_0003` and RLS policies**

Create all four tables, indexes, composite foreign keys, and forced RLS. No migration imports application models or calls `metadata.create_all`.

- [ ] **Step 6: Run SQLite and real PostgreSQL contract tests**

```powershell
C:\Python313\Scripts\uv.exe run pytest tests/test_memory_repository.py tests/test_memory_unit_of_work.py -q
C:\Python313\Scripts\uv.exe run pytest tests/integration/test_postgres_memory.py -q -m integration
```

When Docker is absent, the second command must skip explicitly and the first command must still pass.

- [ ] **Step 7: Commit**

```powershell
git add fairy-v3/core/src/fairy_core/memory fairy-v3/core/src/fairy_core/persistence fairy-v3/core/tests fairy-v3/cloud/migrations fairy-v3/cloud/tests
git commit -m "feat(v3): persist canonical Hermes memory"
```

---

### Task 7: Route Hermes Memory Mutations Through Commands and Public Contracts

**Files:**
- Create: `fairy-v3/core/src/fairy_core/memory/application.py`
- Modify: `fairy-v3/core/src/fairy_core/commanding/registry.py`
- Modify: `fairy-v3/core/src/fairy_core/contracts/models.py`
- Modify: `fairy-v3/core/src/fairy_core/contracts/methods.py`
- Modify: `fairy-v3/core/src/fairy_core/application/service.py`
- Modify: `fairy-v3/cloud/src/fairy_cloud/api.py`
- Modify: `fairy-v3/desktop/src/core/client.ts`
- Modify: `fairy-v3/desktop/src/core/contracts.ts`
- Test: `fairy-v3/core/tests/test_memory_application.py`
- Test: `fairy-v3/core/tests/test_memory_contracts.py`
- Test: `fairy-v3/cloud/tests/test_http_contract.py`
- Test: `fairy-v3/desktop/src/core/client.test.ts`

**Interfaces:**
- Produces commands: `memory.observe`, `memory.claim.promote`, `memory.claim.supersede`, `memory.claim.resolve_conflict`, and `memory.forget`.
- Produces CoreClient groups: `memory.observations`, `memory.claims`, and `memory.forget`.
- Emits typed memory lifecycle events through the canonical Event Ledger.

- [ ] **Step 1: Write failing policy/command tests**

Assert direct repository access is absent from renderer-facing handlers, model-supplied tenant/Scope fields are ignored, Project Canonical promotion requires approval, and replay with the same fingerprint is idempotent.

- [ ] **Step 2: Register memory ToolDefinitions with exact policy metadata**

Observe is low-risk for Conversation Draft only. Canonical promotion, conflict resolution, supersede, and forget require explicit approval. Every mutation requires the Core-injected Scope digest.

- [ ] **Step 3: Implement `MemoryApplication` on CoreUnitOfWork**

Each method validates contracts, submits a Command, writes canonical memory and a typed event in one Unit of Work, and completes/fails the Command with stable errors. It never invokes embeddings or FTS inside the transaction.

- [ ] **Step 4: Add transport-neutral methods and generated contracts**

Add request/response Pydantic models and method catalog entries. FastAPI and JSON-RPC consume the same handlers. Regenerate OpenAPI and TypeScript; do not hand-edit generated declarations.

- [ ] **Step 5: Add client methods and runtime contract tests**

The TypeScript client receives typed methods for explicit remember, inspect Claims, resolve conflict, and forget. Zod validation rejects malformed memory envelopes at the cloud boundary.

- [ ] **Step 6: Verify contracts and commit**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\generate-contracts.ps1
C:\Python313\Scripts\uv.exe run pytest
npm test -- --run
npm run build
git add fairy-v3
git commit -m "feat(v3): expose governed Hermes memory commands"
```

---

### Task 8: Add Concurrency, Recovery, Security, and Production Assembly Gates

**Files:**
- Create: `fairy-v3/core/tests/test_memory_properties.py`
- Create: `fairy-v3/core/tests/test_persistence_recovery.py`
- Create: `fairy-v3/cloud/tests/integration/test_command_concurrency.py`
- Create: `fairy-v3/cloud/tests/integration/test_memory_concurrency.py`
- Create: `fairy-v3/cloud/tests/integration/test_production_events.py`
- Modify: `fairy-v3/cloud/tests/test_deployment_contract.py`
- Modify: `fairy-v3/scripts/check_boundaries.py`
- Create: `fairy-v3/scripts/test-all.ps1`
- Modify: `fairy-v3/README.md`

**Interfaces:**
- Produces: one deterministic full verification command.
- Produces: executable proof of no cross-tenant leakage, stale-fence completion, partial commits, or production SSE gaps.

- [ ] **Step 1: Add two-connection concurrency tests**

Cover Task and Command idempotency races, Claim revision CAS, duplicate memory event fingerprints, task sequence allocation, one-worker claim, lease expiry, and stale fence completion rejection.

- [ ] **Step 2: Add crash injection tests at every persistence phase**

Inject failure before intent commit, after intent commit, during external operation, before result commit, after event insert, and before outbox insert. Assert rollback or explicit resumable/interrupted state without duplicate side effects.

- [ ] **Step 3: Add security tests**

Cover RLS, tenant predicates, Conversation Draft isolation, malicious Scope fields, secret-like memory, invisible Unicode controls, prompt-injection text, and forgotten Claim suppression.

- [ ] **Step 4: Add production Cloud event test**

Run the real app composition with PostgreSQL stores, create a Core command and memory Claim, reconnect SSE with `Last-Event-ID`, and assert ordered deduplicated command and memory events.

- [ ] **Step 5: Create `scripts/test-all.ps1`**

The script runs boundary checks, Core lint/tests, Cloud lint/unit tests, offline Alembic DDL, Rust fmt/clippy/tests, desktop tests/build, contract regeneration with clean diff, and optional Docker integration when `docker version` succeeds.

- [ ] **Step 6: Run the complete verification matrix**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-all.ps1
```

Expected: all locally available gates pass. Docker-only tests report explicit skips when Docker is not installed.

- [ ] **Step 7: Commit**

```powershell
git add fairy-v3
git commit -m "test(v3): gate Hermes persistence recovery"
```

---

### Task 9: Remove Boundary Leakage and Close the Foundation Milestone

**Files:**
- Modify: `fairy-v3/core/pyproject.toml`
- Modify: `fairy-v3/core/uv.lock`
- Modify: `fairy-v3/cloud/src/fairy_cloud/worker.py`
- Create: `fairy-v3/cloud/src/fairy_cloud/workers/__init__.py`
- Create: `fairy-v3/cloud/src/fairy_cloud/workers/outbox.py`
- Modify: `fairy-v3/cloud/src/fairy_cloud/main.py`
- Modify: `fairy-v3/docs/architecture.md`
- Modify: `fairy-v3/docs/legacy-baseline.md`
- Modify: `fairy-v3/docs/legacy-intake.md`
- Modify: `fairy-v3/docs/adr/0003-tenant-unit-of-work-and-canonical-ledger.md`
- Modify: `fairy-v3/README.md`

**Interfaces:**
- Removes unused cloud/web dependencies from the Core package.
- Renames the current outbox-only worker so it cannot be confused with the future OCI execution worker.
- Records exact delivered and deferred Hermes slices.

- [ ] **Step 1: Prove unused Core dependencies have no imports**

```powershell
rg -n "^(from|import) (aiosqlite|alembic|asyncpg|fastapi|httpx|uvicorn)" fairy-v3/core/src fairy-v3/core/tests
```

Expected: no matches.

- [ ] **Step 2: Remove unused Core dependencies and regenerate only `core/uv.lock`**

Keep Core dependencies to Pydantic and SQLAlchemy plus actual runtime needs. Keep Alembic, asyncpg, FastAPI, httpx, uvicorn, boto3, auth, and psycopg in Cloud.

- [ ] **Step 3: Rename outbox worker ownership**

Move `OutboxWorker` to `fairy_cloud.workers.outbox`; keep `fairy_cloud.worker` as a one-release compatibility re-export and update the worker entry point.

- [ ] **Step 4: Run structure and boundary checks**

```powershell
C:\Python313\Scripts\uv.exe run --project fairy-v3/core python fairy-v3/scripts/check_boundaries.py fairy-v3
rg -n "build_local_dispatcher|SqliteStateStore|SqliteCommandLedger" fairy-v3/cloud/src
```

Expected: boundary check passes and Cloud source contains no local/SQLite composition references.

- [ ] **Step 5: Update architecture and milestone documentation**

State that this plan delivers canonical persistence and Hermes Observation/Claim lifecycle only. Full-text Snapshot building, Episodes/pgvector ranking, and multi-device memory controls remain separate approved design slices, not partially implemented claims.

- [ ] **Step 6: Run final verification, request whole-branch review, and commit fixes**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File fairy-v3\scripts\test-all.ps1
git status --short
```

Dispatch a final reviewer over `git merge-base main HEAD..HEAD`, fix every Critical/Important finding with focused tests, rerun the matrix, then commit:

```powershell
git add fairy-v3
git commit -m "chore(v3): close Hermes persistence foundation"
```
