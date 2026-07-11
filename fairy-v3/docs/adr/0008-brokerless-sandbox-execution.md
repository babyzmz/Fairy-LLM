# ADR 0008: Brokerless Sandbox Execution

- Status: Accepted
- Date: 2026-07-12

## Context

Fairy must run dependency installation, bounded project commands, executable
review, and dynamic Preview processes without exposing the Windows host shell
or turning the event-projection worker into a project executor. Local and cloud
execution must preserve the same Scope, policy, approval, CommandRun, recovery,
and artifact contracts. The cloud design also excludes Redis, NATS, a Docker
socket, and host project mounts.

One-shot commands and long-running Runtime processes have different recovery
semantics. A command can finish with a bounded result, while a Runtime needs a
durable handle, lease fence, health probe, route, and explicit stop operation.
Replaying either operation after an uncertain external effect could duplicate
writes or start a second process.

## Decision

1. PostgreSQL is the cloud queue and lease authority. Execution jobs, Runtime
   leases, worker heartbeats, results, and the transactional Outbox use the
   same database and do not require a message broker.
2. Local generic execution is available only through an attested WSL2
   `FairySandbox`. Cloud execution uses dedicated non-root OCI `execution` and
   `runtime` services. Health failure removes `run.sandboxed` from the effective
   capability manifest.
3. Core creates every execution request from an immutable Task Scope and a
   Core-owned template. Models cannot provide a shell string, executable,
   working root, environment, network policy, endpoint, credential, or lease.
4. One-shot jobs and long-running Runtime processes use separate durable state
   machines. Both claim leases with monotonically increasing fences and reject
   stale acknowledgements. An uncertain side effect is interrupted rather than
   automatically replayed.
5. Workers receive a bounded managed Version archive, not a host project mount.
   Dependency layers are content-addressed; Review and Runtime mount them
   read-only. Containers drop all Linux capabilities, use read-only root
   filesystems and private tmpfs, and have no Docker socket or host namespace.
6. Dynamic Preview URLs are opaque, expiring capability routes. The API
   validates tenant, Task, Version, Runtime, Preview, route expiry, and current
   lease fence before proxying to a private worker endpoint.
7. The Outbox worker remains projection-only. It has no project archive,
   dependency layer, provider credential, S3 credential, or execution API.

## Recovery Rules

- Durable intent is committed before worker dispatch.
- Identical idempotency keys reuse the same request fingerprint and entity.
- A live lease cannot be reclaimed by another Core or worker instance.
- An expired lease may be reclaimed only with a higher fence.
- A crash after process start without a durable result becomes
  `WORKER_INTERRUPTED`; Fairy does not guess whether the effect is replayable.
- Runtime recovery probes only the durable opaque handle. Handle, endpoint,
  Scope, generation, or route rebinding fails closed.
- Cancellation and stop requests target the current durable identity and fence;
  they never search for or kill an arbitrary host process.

## Consequences

The design keeps deployment smaller and makes database transactions the single
ordering boundary, but PostgreSQL availability and careful lease indexing are
now operational requirements. Long-running workloads require a distinct
Runtime service instead of reusing the command worker. Real PostgreSQL, S3,
container isolation, and WSL attestation remain mandatory release-environment
gates; unit simulations cannot establish those properties.
