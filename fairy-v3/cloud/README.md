# Fairy Cloud

The cloud boundary contains the FastAPI REST/SSE adapter, PostgreSQL 18 sync
ledger, transactional outbox, fenced Worker leases, immutable S3-compatible
objects, and OIDC resource-server verification. Domain behavior remains in
`core/`.

## Development services

Create a local `.env` from `.env.example`, then build and start the complete
brokerless stack:

```powershell
docker compose up --build -d --wait
```

The stack contains separate migration, API, and Outbox Worker containers. API
and Outbox Worker run as UID/GID 10001 with read-only root filesystems, all Linux
capabilities dropped, no host filesystem mounts, and no Docker socket. The
stateful dependencies are PostgreSQL 18.4, SeaweedFS 4.39 as local S3, and
`mock-oauth2-server` 4.0.0 for Authorization Code + PKCE testing.

Alembic uses the PostgreSQL owner role. The API uses `fairy_app` with forced
RLS, while the cross-tenant `fairy_worker` role can only update the outbox and
manage Worker leases. The idempotent `postgres-permissions` service reapplies
those grants after every migration, including when an existing volume is used.
The API keeps an asyncpg pool for async sync/SSE paths and a psycopg pool for
the synchronous CoreService; both use the same canonical PostgreSQL tables.
Hermes Observations, Claims, revisions, and tombstones are relational source
data protected by the same forced tenant RLS. Lexical search documents,
projection checkpoints, immutable Memory Snapshots, ordered Snapshot items,
and access logs use the same tenant keys and forced RLS. PostgreSQL generates
the `tsvector` column and maintains its GIN index; these search rows remain
rebuildable projections and are never memory authority. Embeddings and
pgvector semantic expansion are not part of the delivered lexical slice.
`FAIRY_CORE_DATA_DIR` contains tenant workspace files only and never SQLite
state in Cloud composition.

Assistant Messages, Turns, Tool Invocations, and Message sequence rows use the
same PostgreSQL tenant key and forced RLS. Alembic revision `20260711_0008`
adds those tables. Local JSON-RPC and Cloud REST expose the same create/get/
cancel/run/retry/list contracts; Turn creation requires an idempotency key and
cannot accept client Scope or Memory bindings. Tenant service resolution
periodically reconciles Assistant and Runtime work. A live linked Command lease
is preserved; an expired lease is reclaimed with a higher fence and terminates
the Turn explicitly as `WORKER_INTERRUPTED` without replaying model or tool
effects.

- API readiness: `http://127.0.0.1:8088/v1/ready`
- S3 endpoint: `http://127.0.0.1:8333`
- OIDC debugger: `http://host.docker.internal:8090/fairy/debugger`

The identity service is development-only. Production accepts compatible
PostgreSQL 18, S3, and OIDC providers and requires HTTPS.

Model profiles are supplied through `FAIRY_PROVIDER_PROFILES_JSON`. Profiles
contain credential references only; `FAIRY_PROVIDER_SECRET_REFS_JSON` maps
each reference to an explicitly scoped `FAIRY_PROVIDER_SECRET_*` environment
variable. Provider values are never returned by REST or persisted by Core.

Every insert into the canonical `domain_events` ledger is copied into Outbox
by a PostgreSQL trigger in the same transaction. The worker entry point is
`fairy_cloud.workers.outbox`; it is the only module entry point. The worker
validates the complete shared EventEnvelope plus exact tenant/event identity
before delivery. Typed projection handlers receive event ID, attempt, and
lease fence for idempotency. This non-root service has no project mount, host
mount, or Docker socket and must not be treated as an OCI project executor.

Apply the cloud schema only through Alembic:

```powershell
uv run alembic upgrade head
```

Run the real PostgreSQL/S3 adapters inside the same network:

```powershell
docker compose --profile test run --build --rm integration
```

This profile also runs canonical Core, Memory Snapshot/FTS, Runtime/Preview
revision and partial-uniqueness checks, same-ID RLS, command/Outbox atomicity,
Assistant crash recovery at each durable phase, capability Outbox delivery,
SSE resume/de-duplication, offline upload, two-device candidate conflicts,
document/evidence isolation, migration, and generated-`tsvector` integration tests
against PostgreSQL 18.4. A local static/unit pass is not a substitute for this
gate; `scripts/test-all.ps1` prints an explicit skip when Docker CLI or the
daemon is unavailable.

Cloud REST exposes the same Runtime/Preview/Artifact contracts as local
JSON-RPC. The current cloud Runtime executor intentionally returns
`SANDBOX_UNAVAILABLE` unless a dedicated non-root OCI executor is composed and
healthy; the Outbox Worker cannot be used as one and there is no API-process or
host-shell fallback.

The integration DSN must point to a dedicated test database. No Redis or NATS
service is required; PostgreSQL owns leases and the transactional outbox.

Stop the stack with `docker compose down`. Add `--volumes` only when the local
PostgreSQL, object, and Core development data should also be removed.
