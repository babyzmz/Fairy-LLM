# ADR 0006: Bind Assistant and Capability Adapters to Durable Tasks

- Status: accepted
- Date: 2026-07-11

## Context

Fairy supports ordinary chat, project work, model providers, research,
information lookup, documents, voice, perception, and narrowly typed host
actions. If a renderer or provider adapter owns the conversation state, chooses
Scope fields, or invokes an effect directly, recovery and multi-device behavior
become impossible to audit. Provider streams and tool calls can also stop after
any persisted phase.

## Decision

Every assistant request binds a Core-created Task, Scope digest, and immutable
Hermes Snapshot before a Turn is created. Messages, Turns, Tool Invocations,
CommandRuns, and public events are tenant-scoped durable records. Model tool
calls are untrusted candidates. Core validates them against the single
ToolDefinition registry, injects Scope, applies policy, and dispatches the
capability behind a CommandRun.

Provider, web, information, document, voice, and perception implementations
live in `fairy-capabilities`. Core owns their ports and provider-neutral types;
Cloud and local stdio composition inject adapters. Credential references cross
composition, but credential values never enter Core state, contracts, events,
prompts, logs, or artifacts.

An active Turn is live only while a linked model or tool CommandRun has a valid
lease. Cloud tenant resolution periodically reconciles work. It preserves live
leases, reclaims expired leases with a higher fence, marks the Turn and active
Tool Invocation `WORKER_INTERRUPTED`, interrupts the CommandRun, and emits one
public `assistant.turn.failed` event. A terminal Turn is never replayed. Retry
creates a new Turn; idempotent tool Commands keep their existing command key.

PostgreSQL inserts every capability event into Outbox in the same transaction.
The Outbox worker validates the shared EventEnvelope and exact tenant/event
identity before dispatch. Projection handlers receive event ID, attempt, and
lease fence as idempotency context. No Redis, NATS, or Docker socket is used.

## Consequences

- Local JSON-RPC and Cloud REST/SSE expose one generated method and event
  contract.
- A renderer refresh, API restart, or second device cannot become conversation
  authority or steal a live Turn.
- External providers remain replaceable and secrets remain outside Core.
- Recovery is explicit rather than automatically rerunning an uncertain model
  or tool effect.
- Local event polling and Cloud SSE polling use a 25ms interval to satisfy the
  100ms event-to-UI p95 release budget.

## Verification

SQLite contract tests cover lease-aware Turn recovery and unique failure
events. The Docker integration profile contains PostgreSQL tests for three
persisted crash phases, Tool Invocation recovery, event/Outbox atomicity,
capability delivery, SSE resume/de-duplication, and two-device version
conflicts. Those tests must be reported as skipped when a Docker daemon is not
available.
