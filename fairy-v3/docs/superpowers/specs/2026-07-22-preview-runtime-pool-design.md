# Preview Runtime Pool Design

## Summary

Fairy automatically activates a runnable Preview whenever the user enters a
chat. Preview processes remain Core-owned: Desktop only reports which
Conversation is active, while Core detects the bound Workspace Version,
selects a trusted Runtime adapter, enforces the device-wide capacity, and
starts, reuses, probes, or stops the Runtime.

The local device supports at most three active Preview Runtimes. A Preview that
is no longer selected remains warm for ten minutes. Activating a fourth
Preview stops the least recently accessed eligible Runtime before starting the
new one.

## Goals

- Automatically load runnable website Previews when entering ordinary and
  project Conversations.
- Support static sites, Vite applications (including React), Next.js, Astro,
  Node services, Python ASGI services, and existing multi-service Runtime
  manifests.
- Reuse healthy Runtimes across chat switches without starting duplicate Node
  processes.
- Bound local process, port, and memory use with deterministic capacity and
  idle eviction.
- Preserve Workspace, Version, Task, Preview, and execution-target binding.

## Non-goals

- React does not execute `npm run`, Shell, or process-management commands.
- Fairy does not run arbitrary package scripts inferred from model output.
- This milestone does not add another Runtime adapter or change the existing
  dependency installation policy.
- A Preview eviction does not delete files, Versions, Artifacts, or Task state.

## Capacity Policy

- Device-wide active Preview capacity: three.
- Warm idle timeout: ten minutes after the Conversation is no longer selected.
- All Preview kinds count toward the same capacity for predictable behavior.
- The selected Conversation is never an eviction candidate.
- A Runtime in `starting` or `stopping`, or bound to an active Assistant Turn,
  pending Approval, or active execution step, is protected from eviction.
- If all three slots are protected, activation enters `waiting_for_slot` and
  retries after the next durable Runtime or Turn event.
- Eligible victims are ordered by `last_accessed_at`, then Preview ID for a
  deterministic tie-break. Core stops the oldest victim before starting the
  requested Preview.

## Domain Model

`PreviewSession` gains durable `last_accessed_at`. Runtime lifecycle state
remains on `RuntimeSession`; access metadata must not masquerade as a Runtime
revision or health transition.

New activation contracts:

- `PreviewActivateInput`: Task, Workspace, Version, expected Workspace
  revision, selection token, and idempotency key.
- `PreviewActivationModel`: outcome, selected Preview context, capacity,
  active count, optional evicted Preview ID, and a safe public reason.
- `PreviewActivationOutcome`: `ready`, `started`, `restarted`,
  `not_runnable`, `waiting_for_slot`, or `failed`.

The activation operation is serialized with the existing Runtime operation
lock. Capacity selection, access touch, victim selection, and durable stop
intent are fenced by current revisions. External process start and stop remain
recoverable through existing CommandRun and Runtime leases.

## Activation Flow

1. Desktop resolves the selected Conversation's active Task, Workspace, and
   target Version.
2. Desktop calls `previews.activate` once for the resulting identity digest.
3. Core verifies the Scope and Workspace revision.
4. Core selects the existing trusted Runtime template. An unsupported or
   absent entrypoint returns `not_runnable` without creating a process.
5. A healthy ready Preview is probed, touched, and returned.
6. A stopped, interrupted, or failed Preview is restarted with the existing
   fenced restart identity where legal.
7. Before creating a new active Runtime, Core reaps idle eligible Runtimes and
   enforces the three-slot capacity with LRU eviction.
8. Core starts the Preview and returns its durable context. Desktop refreshes
   the Preview, Runtime health, and slot projection.

Repeated activation for the same selection token and Scope is idempotent.
Late results are ignored by Desktop if the selected Conversation identity has
changed.

## Runtime Detection

Existing trusted adapters remain authoritative:

- `index.html` without a supported dependency manifest: static Preview.
- Vite dependency plus a supported lockfile: Vite adapter.
- Next.js dependency plus a supported lockfile: Next adapter.
- Astro dependency plus a supported lockfile: Astro adapter.
- `fairy.runtime.json`: governed Node, Python ASGI, or multi-service graph.

Adapters invoke pinned local binaries with exact loopback and leased ports.
They do not execute arbitrary `scripts` entries from `package.json`.

## Desktop Behavior

- Selection of an ordinary or project Conversation triggers activation after
  Task, Workspace, and Version queries succeed.
- No Task, no target Version, or no runnable entrypoint is a quiet no-op.
- The Preview pane shows `Starting <adapter>`, `Ready`,
  `Waiting for runtime slot`, `Paused to free resources`, or the existing
  recoverable failure state.
- The toolbar exposes the adapter and slot usage, for example `Vite - 2/3`.
- The Preview iframe loads automatically once the Runtime is ready.
- Manual Start, Restart, Stop, Browser, and external-open controls remain.
- Switching rapidly cannot start a Runtime for a stale Conversation.

## Idle Reaping

Core owns idle cleanup. A lightweight scheduler wakes on activation and
durable Runtime events and performs a bounded periodic sweep while Core is
running. It stops eligible Runtimes idle for at least ten minutes. Core startup
also reconciles recoverable Runtimes and immediately reaps expired sessions.

Desktop disconnection or window closure does not orphan processes; shutdown
continues to close Core-owned resources through the existing launcher tree.

## Errors And Recovery

- A failed victim stop aborts the new activation and preserves both durable
  states; no fourth Runtime is started.
- Dependency, entrypoint, health, and Sandbox failures use existing public
  error codes and remain retryable from Preview.
- A Core crash between stop intent and external stop is recovered by the
  existing Runtime recovery path.
- A Core crash after start intent cannot create a duplicate Runtime because
  activation and Preview start both use durable idempotency keys.
- Stopping an evicted Preview never changes Task completion or Version state.

## Persistence And Transport

- SQLite and PostgreSQL add non-null `last_accessed_at`, initially populated
  from `updated_at` for existing Preview rows.
- An index supports tenant, active status, and access-time ordering.
- Local JSON-RPC and Cloud REST use the same activation contract. Local device
  capacity is enforced by the Core that owns the executor; it is not supplied
  by Desktop or the model.
- `previews.activate` is not model-visible.

## Test Plan

- Domain: access timestamp invariants and deterministic LRU ordering.
- Core: ready reuse, restart, not-runnable no-op, fourth activation eviction,
  protected Runtime handling, failed stop rollback, idle reaping, and
  idempotent concurrent activation.
- Runtime: Vite/React, Next, Astro, static, Node, Python, and multi-service
  template detection remain governed.
- Persistence: SQLite/PostgreSQL migration, timestamp backfill, index, and
  repository contract parity.
- Desktop: ordinary/project chat entry, rapid switching, stale completion,
  slot waiting, automatic iframe load, and manual controls.
- Recovery: Core interruption during eviction, start, stop, and health probe.
- Process: repeated switching never increases active child-process count past
  the three-Runtime policy and leaves no Node process after shutdown.

## Acceptance Criteria

- Entering a runnable chat automatically produces a ready Preview without a
  manual Start click.
- Returning within ten minutes reuses the same healthy Runtime and port.
- Opening a fourth runnable chat stops the least recently accessed eligible
  Runtime before starting the new one.
- An active or protected Runtime is never evicted.
- React/Vite and existing supported Runtime types start through FairySandbox
  with health checks and leased loopback ports.
- Rapid chat switching cannot display or start a Preview for the wrong Scope.
- Core restart and application shutdown do not leak Runtime child processes.
