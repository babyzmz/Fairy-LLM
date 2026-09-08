# Workflow failure projection authority

Failure projection changes the public Trace, not ownership or outcome of an
external Command. A Workflow failure must not borrow another worker's current
lease/Fence to emit Trace events, nor fail to settle merely because that lease
expired. Events emitted without the original execution claim use scoped domain
projection events and retain the Command reference as payload metadata.

Acceptance uses real temporary SQLite ledgers, two separate conversations and
Commands, an expired original lease and a takeover by another owner. Only the
selected Trace changes; both Command leases and the other Trace remain unchanged.
The event retains Turn/Trace/Command identity and contains only public diagnostics.
This does not assert an external operation physically stopped or grant a retry.
The opt-in Workflow failure integration must also settle its own Turn and Run.

Mock boundaries: no real Provider or OS side effects. Native GUI, real Provider
outcome recovery and PostgreSQL execution are separate gates.

## Evidence

- Before the fix, an expired lease raised `WorkerFenceError` during projection;
  a taken-over lease incorrectly emitted a Command-owned event.
- Both lease cases and a real service/SQLite deferred-media failure passed (3
  tests). Domain failure preserves the completed media artifact and does not
  repeat the Provider call.
- Full Assistant regression: `pytest tests/assistant -q --tb=short`, 320 passed
  in 250.44 seconds. This includes legacy and opt-in engines; engine 4 remains
  opt-in pending the remaining recovery gates.
