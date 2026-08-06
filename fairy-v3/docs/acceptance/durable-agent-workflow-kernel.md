# Durable Agent Workflow Kernel Acceptance

## Observable behavior

- A tool-using Turn returns promptly, continues in the background, survives restart, and emits
  exactly one final Assistant message.
- Independent safe reads from one model round can run concurrently; conflicting or effectful
  calls remain ordered and their results return to the model in original call order.
- Pause, resume, cancel, approval, timeout, and crash recovery settle without an infinite spinner
  or a duplicate side effect.
- During active work the Composer clearly updates the current task. The new instruction takes
  effect at a node boundary and prior completed facts remain visible.
- Closing to tray does not stop local work. Exiting pauses or releases local work for the next
  launch. Cloud work never silently moves to or from the local device.
- Browser research supports governed interaction and bounded downloads but rejects secrets and
  external mutations prohibited by the accepted design.

## State ownership and scope keys

| State | Owner | Scope key | Rule |
|---|---|---|---|
| Run and active plan | Workflow repository | `tenant_id + run_id` | one immutable execution target |
| Plan revision | Workflow repository | `run_id + revision` | append-only |
| Node | Domain adapter + Workflow repository | `run_id + revision + node_id` | versioned payload |
| Attempt lease | Workflow repository | `node_id + attempt + owner + fence` | late owners cannot settle |
| Command/effect | Command Bus | `tenant_id + command_run_id` | existing policy remains authoritative |
| Assistant projection | Assistant ledger | `conversation_id + turn_id` | one Run per new Turn |
| Steering instruction | Workflow repository | `run_id + idempotency_key` | consumed by at most one revision |

## Invariants

- A Run, node, or attempt never crosses tenant, conversation, Task, Project, Version, or execution
  target scope.
- A node becomes ready only when all dependencies succeeded or its adapter explicitly accepts a
  terminal dependency outcome.
- Claim, heartbeat, settle, retry, cancellation, and revision changes are fenced transactions.
- Approval and delayed work hold no worker thread or active execution lease.
- Unknown tools are serial. Only explicit idempotent read/no-effect tools may run in parallel.
- Writes and uncertain effects are never replayed automatically after interruption.
- Old and new schedulers cannot claim the same Turn.
- Realtime and Preview idle maintenance never appear as Workflow nodes.

## Acceptance scenarios

1. Two conversations start long Turns, switch rapidly, and receive only their own nodes, events,
   approvals, Sources, and final messages before and after restart.
2. Two independent delayed reads overlap and improve median elapsed time by at least 25 percent;
   two Browser actions sharing a Session remain serial.
3. A worker dies before and after Command completion. The first case retries safely; the second
   case records or requests recovery without duplicating the effect.
4. Approval arrives while a lease is lost or a pause is requested. Exactly one later ready node
   is created and the old owner cannot settle it.
5. Steering arrives with valid, duplicate, stale, and cross-Turn revisions. Valid guidance creates
   one plan revision; duplicate is replayed; stale and foreign requests are rejected.
6. Normal and deep budgets enforce their model/tool/time caps and use existing approval for an
   extension.
7. App close-to-tray continues work; graceful exit and crash both recover; cancellation remains
   terminal across reopen.
8. Browser rejects expired element references, password/token/OTP controls, dangerous submission,
   oversized downloads, and executable auto-open while allowing research/search/filter flows.
9. Knowledge and Media jobs recover through the shared Kernel without occupying a worker during
   a delayed sync or video poll.

## Required evidence

- Unit/property tests for state transitions, dependency readiness, resource conflicts, budgets,
  retries, instructions, and deterministic result ordering.
- SQLite close/reopen tests with two scopes and PostgreSQL claim/RLS integration tests.
- Assistant scripted-provider tests for pure chat, parallel tools, approval, failure, recovery,
  steering, and final-message uniqueness.
- Desktop Vitest and Playwright at normal and constrained sizes for Activity Rail, Composer mode,
  Line Sidebar, Preview, focus, keyboard, IME, and Reduced Motion.
- Browser Worker smoke and local fixture E2E for element references, navigation, forms, tabs,
  download containment, interruption, and recovery.
- Real Auto and Manual Provider checks, real FairySandbox WSL attestation, real Cloud PostgreSQL/S3,
  and native Tauri WebView2. Missing environments are reported as unverified, never inferred.

## Automation boundaries

- Scripted providers prove scheduling and hard gates, not real model tool selection quality.
- SQLite proves persistence semantics but not PostgreSQL locking or RLS.
- Playwright proves browser UI behavior but not WebView2 focus, tray, or native file opening.
- A fixture Browser Worker does not prove Edge process crash recovery.
- Docker, Tauri release, and production image builds are outside development verification.

