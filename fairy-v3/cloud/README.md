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

The stack contains separate migration, API, and Worker containers. API and
Worker run as UID/GID 10001 with read-only root filesystems, all Linux
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
data protected by the same forced tenant RLS. Search and embedding data remain
rebuildable projections and are not part of this persistence slice.
`FAIRY_CORE_DATA_DIR` contains tenant workspace files only and never SQLite
state in Cloud composition.

- API readiness: `http://127.0.0.1:8088/v1/ready`
- S3 endpoint: `http://127.0.0.1:8333`
- OIDC debugger: `http://host.docker.internal:8090/fairy/debugger`

The identity service is development-only. Production accepts compatible
PostgreSQL 18, S3, and OIDC providers and requires HTTPS.

Apply the cloud schema only through Alembic:

```powershell
uv run alembic upgrade head
```

Run the real PostgreSQL/S3 adapters inside the same network:

```powershell
docker compose --profile test run --build --rm integration
```

The integration DSN must point to a dedicated test database. No Redis or NATS
service is required; PostgreSQL owns leases and the transactional outbox.

Stop the stack with `docker compose down`. Add `--volumes` only when the local
PostgreSQL, object, and Core development data should also be removed.
