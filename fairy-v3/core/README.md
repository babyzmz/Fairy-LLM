# Fairy Core

The transport-independent domain and application core for Fairy V3.

Hermes memory uses canonical Observation, Claim, revision, and tombstone
records behind one tenant-scoped `MemoryRepository`. SQLite and PostgreSQL
share the SQLAlchemy adapter and participate in the same `CoreUnitOfWork` as
project state and the Command Ledger.

```powershell
C:\Python313\Scripts\uv.exe sync --dev
C:\Python313\Scripts\uv.exe run pytest
C:\Python313\Scripts\uv.exe run ruff check src tests
```
