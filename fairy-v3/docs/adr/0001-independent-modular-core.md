# ADR 0001: Independent Modular Core With Isolated Workers

Status: accepted

## Context

The legacy application concentrates API composition, routing, execution, and
session state in large modules. Fairy V3 needs durable project execution,
local/cloud parity, strong Scope enforcement, and personal multi-device sync.

## Decision

Build Fairy V3 as a new application. Implement a transport-independent Python
Core organized around domain and application boundaries. Use a Rust local
worker for privileged Windows operations, a dedicated WSL2 provider for
generic shell execution, and an OCI cloud worker. Keep local JSON-RPC and cloud
REST/SSE as adapters over the same application services and contracts.

Use a modular deployment first. PostgreSQL leases and a transactional outbox
provide cloud dispatch and event delivery; Redis, NATS, and microservice splits
are deferred until measured load requires them.

## Consequences

- Legacy code is not a runtime dependency.
- Domain behavior can be tested without desktop, database, or network layers.
- Local and cloud transports must pass identical contract tests.
- Privileged execution remains replaceable and platform-specific.
- Initial implementation includes more explicit contracts and adapters, but
  avoids duplicated business rules and premature distributed services.
