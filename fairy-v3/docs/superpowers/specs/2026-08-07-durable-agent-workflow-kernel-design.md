# Fairy V3 Durable Agent Workflow Kernel Design

**Status:** Accepted implementation specification

**Date:** 2026-08-07

## Purpose

Fairy already has durable Assistant, Media, and Knowledge workers, but each domain repeats its
own claim, lease, heartbeat, cancellation, and recovery loop. Assistant also executes multiple
tool calls from one model round sequentially and cannot accept a revised instruction while a Turn
is active. This design replaces those duplicated scheduling mechanics with one durable Workflow
Kernel while preserving Command Bus, Scope, Policy, Approval, Evidence, and domain executors as
the authorities for actual effects.

The Kernel manages finite work. Realtime audio/video sessions and Preview idle resource
maintenance remain independent because their continuous and timer-driven lifecycles are not DAG
workflows.

## Durable model

A `WorkflowRun` is bound to one owner and one immutable execution target. Its active plan is an
append-only `WorkflowPlanRevision`; revisions contain `WorkflowNode` records connected by
`WorkflowEdge` dependencies. Every execution is a fenced `WorkflowAttempt`. Mid-run user
guidance is stored as a `WorkflowInstruction` and applied by creating a later plan revision.

Run states are `queued`, `running`, `waiting_for_approval`, `paused`, `completed`, `cancelled`,
and `failed`. Node states are `pending`, `ready`, `running`, `waiting_for_approval`, `succeeded`,
`failed`, `cancelled`, `skipped`, and `superseded`. Terminal transitions are irreversible. A
worker may settle an attempt only while its owner, lease fence, cancellation revision, and active
plan revision still match.

The normalized tables are:

- `workflow_runs`: owner identity, local/cloud target, trigger, nullable parent, status, budget,
  active plan revision, cancellation revision, engine version, and timestamps.
- `workflow_plan_revisions`: immutable reason, instruction reference, and creation metadata.
- `workflow_nodes` and `workflow_edges`: versioned node payloads, dependency graph, retry policy,
  resource keys, availability time, state, and public summary.
- `workflow_attempts`: attempt number, lease owner/fence/deadline, result and evidence references,
  error code, and timing.
- `workflow_instructions`: idempotent user guidance and the revision that consumed it.

Stored node payloads are versioned data, never callables or executable source. Domain adapters
validate payloads and return typed results. The same repository contract supports SQLite and
PostgreSQL, including tenant predicates and PostgreSQL row-level security.

## Scheduling and effects

The scheduler fairly claims ready nodes across Runs. Local defaults are four global workers, two
concurrent nodes per normal Run, and four per deep Run. Waiting approval, delayed polling, and
paused work consume no worker thread. Heartbeats renew both Workflow Attempt and owned Command
leases; a lost lease interrupts local execution and fences late completion.

Tools default to serial. Only an idempotent `none` or `read` tool with an explicit parallel policy
may fan out. Each adapter derives conflict keys for mutable or ordered resources such as Browser
sessions, Workspace versions, MCP servers, and external accounts. Unknown extensions, writes,
execution, approval-bound actions, and uncertain results remain serial. Tool results are joined in
the model's original call order.

The Kernel never executes effects directly. A node calls its domain adapter, which invokes the
existing governed Command Bus and records Command, Evidence, Approval, and Ledger state in the
same unit of work where required. Retries are automatic only when the operation is idempotent or
the prior outcome is known not to have applied. An uncertain side effect pauses for recovery
instead of replaying.

## Assistant adaptation and steering

Every new Assistant Turn receives one Workflow Run. Routing, evidence collection, model rounds,
tool batches, review, verification, and finalization are nodes added by the Assistant adapter.
Simple chat traverses only the minimum route/model/finalize chain. Existing Assistant messages,
Tool Invocations, Trace Steps, approvals, and evidence receipts remain authoritative and are
projected into the Activity Rail.

The public Turn API keeps create/start/get/cancel/retry and adds workflow get, pause, resume, and
steer operations. While a Turn is active, the Composer is explicitly an "update current task"
input. A steering request stops new dispatch, allows already-running safe reads to settle, records
the instruction, supersedes unstarted obsolete nodes, and appends an immutable plan revision.
Completed reads and side effects remain facts in the revised plan.

Normal Runs retain 12 model rounds, 32 tools, and 30 minutes. High-complexity routing selects a
deep budget of 24 model rounds, 96 tools, and two hours. Paid or extended work continues through
the existing approval policy.

## Browser and domain migration

Browser remains a domain executor. Its model-visible surface adds governed waits, history,
hover, selection, check state, tab control, and bounded downloads. Element references bind to
Session, Tab, and page revision. Secret fields and purchase, publish, send, account, or permission
mutations are rejected. Downloads are task-scoped, capped at 50 MiB, hashed, MIME-recorded, and
never automatically opened or executed.

After Assistant parity, Knowledge Sync becomes a Workflow Run and Media Generation becomes
submit/wait/poll/archive nodes. Delayed polls release workers. Related Runs may carry a parent ID
for future compatibility, but this release does not expose general child-Agent orchestration,
scheduled automation, or automatic local-to-cloud migration.

## Compatibility and cutover

Schema changes are additive. An engine version is bound when work is created. Existing terminal
Turns remain readable. Existing non-terminal legacy Turns drain through the legacy scheduler;
new Turns use the Workflow Kernel. A Turn is never claimable by both engines. The compatibility
worker and old queue tables are removed only after no legacy non-terminal work remains and all
local/cloud recovery gates pass.

Closing the main window to tray keeps local work running. Graceful application exit releases or
pauses local work; restart resumes it. A crash is recovered after lease expiry. Cloud work stays
on Cloud. Changing execution target requires a new Run rather than moving an existing one.

## Explicit exclusions

- No global Task Center; workflow controls stay in the owning chat and Activity Rail.
- No scheduled automation or general multi-Agent UI.
- No Realtime stream or Preview pool migration into the Workflow Kernel.
- No secret entry, purchasing, posting, sending, or account/permission mutation through Browser.
- No Docker, release build, or production image during ordinary implementation verification.

