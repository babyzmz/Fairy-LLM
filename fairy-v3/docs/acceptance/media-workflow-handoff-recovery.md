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

### Original-Fence settlement

- RED: all four late success/failure × same/different owner cases accepted the
  stale completion because the adapter borrowed the persisted owner/Fence.
- Fix: Command Bus settlement presents the original execution claim. A stale
  callback cannot change the new claim, including when worker names are reused.
- GREEN: `pytest tests/media tests/test_command_bus.py -q`: 34 passed (31.80s).
  Ruff on the adapter and new regression passed. Both independent commands and
  database reopen are asserted; no existing test assertion was relaxed.
