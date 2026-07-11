# Fairy Core

The transport-independent domain and application core for Fairy V3.

Hermes memory uses canonical Observation, Claim, revision, and tombstone
records behind one tenant-scoped `MemoryRepository`. SQLite and PostgreSQL
share the SQLAlchemy adapter and participate in the same `CoreUnitOfWork` as
project state and the Command Ledger. Lexical search is a disposable adapter:
SQLite uses external-content FTS5 and PostgreSQL uses a generated `tsvector`
with GIN. Every hit is resolved back to a scoped canonical Observation or
Claim before it can become Snapshot content; unresolved projection-only rows
fail closed.

Task creation builds and binds exactly one immutable Memory Snapshot in the
same transaction. `snapshot_builder.py` owns orchestration,
`snapshot_candidates.py` resolves canonical candidates,
`snapshot_scope.py` enforces provenance, `snapshot_rendering.py` escapes and
labels model-visible data, and `snapshot_selection.py` owns deterministic
authority ordering and token budgets. Projection failure returns an explicit
degraded Snapshot from bounded relational fallback and cannot mutate a
Snapshot already bound to a Task.

Runtime orchestration follows the same Core-owned rule. `runtime.py` contains
the start/stop/resolve/recovery lifecycle, `runtime_contracts.py` contains its
application DTOs, and `runtime_support.py` owns command leases, Scope checks,
and persistence lookups. Runtime executor output is untrusted and must match
the durable Runtime handle and exact loopback endpoint before Preview state can
advance. A different Core instance cannot recover a live lease before expiry.

Assistant state uses the same tenant-scoped Unit of Work. Immutable Messages,
fenced Assistant Turns, Tool Invocations, and per-Conversation sequence rows
are persisted in SQLite/PostgreSQL with Task, Scope digest, and Hermes Snapshot
bindings. Public Message pages exclude internal records. The Assistant Ledger
does not import or call a model provider; provider execution remains behind
Core ports and external composition.

The local static executor is read-only. Dynamic WSL and cloud OCI execution
remain unavailable until dedicated executors satisfy their health and
attestation contracts; Core never substitutes a host shell.

Core runtime dependencies are intentionally limited to Pydantic and
SQLAlchemy. FastAPI, Alembic, asyncpg, object storage, and server processes
belong to Cloud; local JSON-RPC uses the Python standard library.

```powershell
C:\Python313\Scripts\uv.exe sync --dev
C:\Python313\Scripts\uv.exe run pytest
C:\Python313\Scripts\uv.exe run ruff format --check src tests
C:\Python313\Scripts\uv.exe run ruff check src tests
```
