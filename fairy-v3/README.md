# Fairy V3

Fairy V3 is a new Windows-first desktop AI application. It is developed in
parallel with the legacy Fairy implementation and does not import legacy
runtime modules or migrate legacy databases.

The product has two first-class workspaces:

- scratch conversations for general assistant tasks;
- project conversations for scoped, versioned project execution.

The architecture is project-first, task-driven, preview-first, and
core-owned. Every side effect crosses the command bus and is recorded in the
durable ledger before execution.

## Repository layout

```text
fairy-v3/
|- core/       Python domain, application services, and transports
|- desktop/    React/Tauri shell and the Rust local worker
|- contracts/  Generated OpenAPI, JSON Schema, TypeScript, and Rust contracts
|- cloud/      Cloud API/worker composition and deployment files
|- resources/  Provenanced character, icon, and voice assets
|- scripts/    Contract generation and repository boundary checks
|- docs/       Architecture, threat model, ADRs, and operating documentation
`- tools/      Isolated contract generators and developer tooling
```

Tests stay with the package that owns the behavior: `core/tests`,
`cloud/tests`, `desktop/src/**/*.test.ts(x)`, and `desktop/e2e`. This keeps
fixtures and runtime dependencies inside their actual boundary.

The implementation is intentionally independent. Legacy character assets,
voice assets, doctrine, and black-box behavior may be used as references only.

## Current milestone

The local vertical slice is executable: Tauri supervises Python Core, Core
dispatches scoped workspace operations to the Rust worker, and the durable
ledger covers import, worktree creation, Changeset approval, review,
checkpoint, accept, discard, capabilities, and resumable events. The Docker
cloud environment, OIDC, PostgreSQL sync/outbox, S3 snapshots, and generated
cloud client exist. Local JSON-RPC and Cloud REST now invoke the same
transport-independent CoreService; Cloud Core state uses the canonical
tenant-scoped PostgreSQL Unit of Work, while `FAIRY_CORE_DATA_DIR` holds only
managed workspace files. Canonical Hermes Observations, Claims, immutable
revisions, and tombstones now share the same SQLite/PostgreSQL Unit of Work.
Governed Memory commands are exposed through the shared Core contract, and
PostgreSQL enqueues every canonical domain event through a transaction-local
Outbox trigger. A disposable lexical projection provides SQLite FTS5 and
PostgreSQL generated-`tsvector` search behind one port. Every Task is bound to
one deterministic, bounded, immutable Memory Snapshot before execution; stale
or failed projections produce an explicit relational-fallback Snapshot instead
of silently reusing context. Search, Snapshot inspection, and projection
health are available through the shared CoreClient contract. Property,
crash-recovery, scope, injection, fence, contract, and production SSE gates
cover the delivered persistence and lexical retrieval slices.

Runtime, Preview, and Artifact state is also durable and transport-neutral.
The Rust Local Worker serves a read-only static candidate Version over exact
loopback without invoking project code, while Core owns start/stop intent,
lease fencing, recovery, Preview resolution, and accept/discard invariants.
React now renders the persisted Task Timeline and sandboxed Preview iframe
through CoreClient; production sample state has been removed.

The next durable product slice is also in place: Task-bound Assistant Messages,
Turns, Tool Invocations, and per-Conversation sequence allocation share the
SQLite/PostgreSQL Unit of Work. Turn creation is idempotent, cancellation and
recovery are fenced, public Message pages exclude internal records, and local
JSON-RPC, Cloud REST, OpenAPI, and CoreClient expose one generated contract.
This ledger does not yet call a model; provider execution is the next slice.

Episodes, pgvector expansion, asynchronous projection rebuild workers,
multi-device memory controls, dynamic WSL/OCI project execution, and remaining
product capability workflows remain separate implementation slices. They stay
unavailable rather than falling back to host execution.

Run every locally available release gate with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-all.ps1
```

The command runs Core and Cloud lint/tests, offline Alembic DDL, Rust checks,
Desktop tests/build, contract regeneration, and repository boundary checks.
When Docker is available it also runs the real PostgreSQL/S3 integration
profile; otherwise it reports those integration tests as explicitly skipped.
The default run also reports WSL verification as skipped. Require a real
FairySandbox attestation with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-all.ps1 -RequireWslSandbox
```

Use `-SkipDocker` when an intentionally local-only gate is required. Static
Preview itself does not require Docker or WSL.

Run only the dependency and layer boundary gate with:

```powershell
uv run --project core python scripts/check_boundaries.py .
```
