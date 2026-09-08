# Media Workflow handoff recovery

## Contract and ownership

Phase 6 must release the Assistant node while a Media domain Run is working or
waiting. The original Task, Scope, Tool Invocation, Command and paid Provider Job
remain authoritative. Recovery reconciles that Job; it must not submit another
generation. Completed images/audio are exposed only after the archive node.
Video start may return its existing active-job receipt, not a completed artifact.

Command leases belong to the worker that claimed them. A late completion or
failure must present its original owner and Fence, never borrow a newer owner's
identity from the database. This invariant applies to both current and new engines.

## Acceptance

- Real SQLite Command lease takeover, late success/failure, same owner with newer
  Fence, and a second independent command: stale settlement is rejected and the
  new owner and other scope stay unchanged. Close/reopen confirms durable state.
- Scripted Media Provider blocks while the parent tool yields its Kernel slot;
  another Run progresses. Success, failure, cancellation and restart retain one
  Job/submission and ordered tool results. No second scheduler or batch pool.
- Archive gating, video receipt semantics, approval and changed intent keep their
  existing contracts. Unknown external effects remain fail-closed.
- Deterministic tests use temporary databases and scripted providers. Real paid
  Provider, native Desktop and hardware acceptance remain separate, unverified
  gates. No user database, credentials, models or media are changed by these tests.

## Evidence

Implementation and regression results are appended below as work progresses.

### Archive-pending process restart

- Inject an interrupted archive after the real Provider result and Artifact commit,
  for two different Task/Workspace bindings. Close the first Core and build a new
  Core over the same temporary database. Both Runs must complete the remaining
  archive nodes with unchanged Job/Artifact IDs and zero new Provider submissions.
- This checks actual service/database close-and-reopen; Provider I/O remains
  scripted. It does not claim paid-provider or native process acceptance.
- RED: both restored Runs remained paused although their Jobs and Artifacts were
  completed. Recovery now optionally includes completed Jobs with a nonterminal
  Media Run, via a tenant-bound SQL existence check. Normal provider-job recovery
  queries/counts are unchanged and archived historical Jobs are excluded.
- GREEN: archive restart + store + step Media **11 passed (24.38s)**;
  complete Media package **27 passed (34.47s)**; Ruff passed. Two separate Tasks
  recovered the same artifact IDs with zero new Provider requests.

### Non-blocking domain handoff (in progress)

- Engine 4 stamps trusted internal Command metadata, atomically publishes the
  existing Job's domain Run and abandons only its original parent lease. The
  Media adapter owns/renews the Command during Provider work, then yields it back.
  Reconciliation reads the same scoped Job and never calls prepare/submit again.
- A one-second persisted wait releases the parent Kernel slot. Its wait-attempt
  bound fits the existing 30-minute/two-hour Run deadline; no extra executor or
  parent-worker reservation. Paid approval and original identities are unchanged.
- RED: parent Tool stayed RUNNING throughout a blocked Provider. GREEN now also
  covers archive gating, lost handoff acknowledgement, foreign Task rejection and
  all three remaining global slots executing independent Runs (6 tests, 17.59s,
  before the cancellation extension). Both old Media failure contract assertions
  stay unchanged; preserving them required stopping fan-in on the original error
  and rejecting another media call after successful generation.
- Cancellation RED: Provider stopped, but the yielded Invocation left
  `cancellation_pending` true indefinitely. The Kernel now returns an idle receipt
  only after the cancelled Run's local adapters return. The Assistant consumes
  this receipt and settles its original Invocation/Command with a fresh fenced
  cancellation lease. Pending remote Provider processing is not claimed stopped.
  The direct cancellation regression passes; broader validation follows.

- Broad deterministic gate: Workflow + Media + step Media/tools + uncertain
  side-effect recovery + cancellation settlement/protocol/cleanup/restart:
  **105 passed, 109.35s**. Ruff passed and `git diff --check` was clean. No existing
  test assertions were changed. The real close/reopen of an archive-pending domain
  Run, engine-4 music/video receipts, and full Assistant/default-switch gates are
  still pending; lost acknowledgement is not presented as process-restart evidence.

### Original-Fence settlement

- RED: all four late success/failure × same/different owner cases accepted the
  stale completion because the adapter borrowed the persisted owner/Fence.
- Fix: Command Bus settlement presents the original execution claim. A stale
  callback cannot change the new claim, including when worker names are reused.
- GREEN: `pytest tests/media tests/test_command_bus.py -q`: 34 passed (31.80s).
  Ruff on the adapter and new regression passed. Both independent commands and
  database reopen are asserted; no existing test assertion was relaxed.

### Engine-4 reopen, music and video receipts

- Core close/reopen at the archive boundary preserves the Assistant Turn/Run,
  Tool Invocation/Command, Media Job/Run and Artifact. A fresh scripted coordinator
  makes exactly one final-response request; the fresh Media provider makes zero
  generation requests. Exactly one Assistant message is recorded.
- Music and video both retain tool-level paid approval. Rejection submits no Media
  requests. Approved music waits for its artifact/archive; approved video returns
  an active Job receipt while its domain Run continues, without an invented artifact.
- RED video gate found a preexisting `ToolResult.create` call missing required
  `artifact_ids`; the active receipt now explicitly uses `()`. Its follow-up model
  policy also incorrectly asserted the output was already durable. A failing policy
  assertion now guards the distinction between a durable receipt and finished output.
- GREEN: Media + step Media + model routing **59 passed (77.46s)**; Ruff passed.
  New tests supplement existing assertions; no original contract was relaxed.
  Prompt assertions are not a substitute for real model output accuracy acceptance.
