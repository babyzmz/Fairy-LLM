# ADR 0005: Allow Host Static Preview, Keep Dynamic Execution Sandboxed

- Status: Accepted; dynamic execution details extended by ADR 0008
- Date: 2026-07-11

## Context

Fairy must show a real candidate Version before acceptance. A static web
project can be inspected without executing project code on Windows, but a
general development server, package manager, interpreter, or shell would cross
the host execution boundary. Treating those cases as equivalent would either
remove useful Preview support or expose model-directed host execution.

Preview start and stop also cross a process boundary. A crash can occur after
durable intent but before Core records the worker result, so endpoint metadata,
idempotency, ownership, and recovery cannot be renderer state.

## Decision

The Rust Local Worker may host one narrowly scoped static file server on
Windows. It reads only a canonical Fairy-managed Version, binds exact
`127.0.0.1` on an ephemeral port, accepts GET and HEAD, rejects traversal,
links, reparse escapes, oversized request targets, and directory listing, and
adds browser security headers. This path never invokes a project process or
host shell.

Core owns Runtime, Preview, and Artifact state. It commits start/stop intent
before dispatch, validates worker handle/host/port/URL/Preview identity, and
commits completion with typed events. Same-key replay reuses the same entity.
Each Core instance has a unique lease owner; another instance waits for lease
expiry before fenced recovery. A missing or rebound process becomes explicitly
`interrupted`.

Dynamic project execution is a different capability. It must use an attested
WSL2 `FairySandbox` or a non-root cloud OCI Worker. Missing health disables the
capability with `SANDBOX_UNAVAILABLE`; there is no Windows shell fallback. ADR
0008 supplies the implemented WSL Runtime supervisor, cloud execution/Runtime
workers, PostgreSQL lease model, and private Preview routing without changing
this static-host boundary.

The desktop consumes only Core-resolved Preview state. Local URLs are accepted
only as exact loopback HTTP with an explicit port; cloud URLs require HTTPS.
The iframe omits `allow-same-origin`, and version acceptance remains a separate
explicit user action.

## Consequences

- Static sites have a useful Preview without Docker, WSL, or project process
  execution.
- Framework development servers and build commands are available only through
  the dedicated, healthy executors defined by ADR 0008.
- Runtime recovery adds durable rows, revision fences, command leases, and
  explicit interrupted states.
- The Local Worker keeps protocol, workspace, Preview server, and error
  responsibilities in separate modules while preserving its public crate API.

## Verification record

On 2026-07-11, `scripts/test-all.ps1 -SkipDocker` passed all available Windows
gates, including Core/Cloud lint and tests, reversible offline Alembic SQL,
the full Rust workspace, Desktop tests/build, and generated contract drift.
Desktop Playwright tests also passed at 880x680 and 640x700 with reduced
motion.

The Docker Compose environment is present, but no Docker CLI was available in
the executing environment; real PostgreSQL 18.4, RLS, Runtime recovery, and S3
integration were therefore skipped, not passed. The real WSL probe returned
`SANDBOX_UNAVAILABLE` because `wsl --status` failed, so the
`-RequireWslSandbox` gate was not reported as passed.
