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

Core runtime dependencies are intentionally limited to Pydantic and
SQLAlchemy. FastAPI, Alembic, asyncpg, object storage, and server processes
belong to Cloud; local JSON-RPC uses the Python standard library.

```powershell
C:\Python313\Scripts\uv.exe sync --dev
C:\Python313\Scripts\uv.exe run pytest
C:\Python313\Scripts\uv.exe run ruff format --check src tests
C:\Python313\Scripts\uv.exe run ruff check src tests
```
