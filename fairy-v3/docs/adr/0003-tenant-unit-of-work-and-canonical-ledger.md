# ADR 0003: Tenant-scoped Unit of Work and canonical ledger

- Status: accepted
- Date: 2026-07-10
- Updated: 2026-07-11

## Context

The first cloud slice persisted synchronization state in PostgreSQL while the
Cloud Core dispatcher reused the local composition and wrote command state to
per-user SQLite files. This created two Project revision authorities and two
event streams. Production SSE read only PostgreSQL sync events, so Core command
events were not visible. Replacing SQLite calls with independent PostgreSQL
calls would still leave partial state when a process crashes between writes.

Fairy also needs cross-tenant workers, one global resumable event cursor,
transactional outbox delivery, and personal accounts that may later belong to
an organization.

## Decision

Use a shared PostgreSQL schema with an opaque internal `tenant_id` on every
owned row. Every primary key, foreign key, idempotency constraint, and task
sequence constraint is tenant-scoped. Adapters include explicit tenant
predicates and PostgreSQL row level security provides defense in depth through
`SET LOCAL app.tenant_id`.

Do not use schema-per-tenant. Shared workers need to claim work across tenants,
events need a global cursor, and duplicating migrations and connection pools
would add operational risk without improving the application boundary.

Define synchronous `StateStore`, `CommandLedger`, `MemoryRepository`, and
`CoreUnitOfWork` ports in Core. Local SQLite and cloud PostgreSQL implement the
same contracts. A Unit of Work owns one database connection and transaction for
related state, command, event, and canonical memory mutations. Cloud uses a
synchronous psycopg SQLAlchemy engine for the synchronous Core; the existing
async engine remains for async-only API and Outbox Worker paths.

Use one canonical Project table for `revision` and `active_version_id`. Remove
the separate cloud project authority. Use one canonical Event Ledger with a
global identity cursor; Core command events and synchronized device events are
written there. The outbox is written in the same transaction as each publishable
event. PostgreSQL enforces this for every producer with an invoker-rights
`AFTER INSERT` trigger from `domain_events` to `outbox`. The sync adapter still
verifies the resulting immutable Outbox payload fingerprint on replay.

Do not keep a transaction open across a file, network, sandbox, or object-store
operation. The Core first commits durable command intent and a fenced lease. It
then executes an idempotent operation outside the transaction. Finally it
commits the resulting domain state, command terminal status, visible events,
and outbox records in one fenced Unit of Work. A stale worker cannot acknowledge
or complete work after losing its lease.

Idempotency keys store a request fingerprint. Reusing a key with a different
command, Scope, or payload is a conflict, not a successful replay. Command
state transitions are compare-and-swap operations. Task event sequences use an
atomic allocator rather than `MAX(sequence) + 1`.

Production PostgreSQL schema changes use Alembic only. SQLite may initialize a
fresh local V3 schema for development. No legacy Fairy database is migrated;
V3 has not shipped a stable database schema, so pre-release developer data may
be discarded when explicitly documented.

## Consequences

- Cloud API composition can no longer call `build_local_dispatcher`.
- REST and JSON-RPC call the same application facade instead of one transport
  constructing requests for another transport.
- Project promotion, Core command events, SSE, and sync conflict handling share
  one transactionally consistent authority.
- PostgreSQL migrations require tenant composite keys, foreign keys, RLS
  policies, command leases, sequence allocation, outbox fencing, and the
  Event-to-Outbox trigger.
- Local and PostgreSQL implementations must pass one state/ledger contract
  suite plus real two-connection concurrency and crash-recovery tests.
- Hermes Observations, Claims, revisions, and Tombstones share the same tenant
  transaction and cannot be replaced by FTS or vector projections.
- The event publisher is named `fairy_cloud.workers.outbox`; the future OCI
  execution Worker remains a separate responsibility.
- The implementation is more explicit, but removes process-local cloud state,
  silent idempotency mismatches, and dual cursor reconciliation.
