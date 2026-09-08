# Deferred Media steering boundary

An already dispatched Media operation remains owned by its original Task, Scope,
Command and domain Workflow. Steering stops new dispatch, but must not supersede
the parent reconciliation node while the external outcome is still pending.
Only a trusted Adapter's persisted reconciliation phase may run while paused;
ordinary nodes, unstarted tools and cancelled/terminal runs remain ineligible.
This phase reads and records the existing result and cannot submit another job.

Once the result is known, preserve its evidence, apply the pending plan revision,
and continue with the revised request. Manual pause records the same facts without
resuming the task. Reconciliation uses the existing Kernel workers, limits, Fence,
resource keys and deadlines, never another executor pool.

Acceptance: real SQLite and service/Kernel with a blocked scripted image Provider;
steer during deferred waiting; no new revision or Provider request before release;
one job, artifact, invocation and final reply afterward. Verify unrelated paused
nodes and a second run remain isolated, cancellation excludes reconciliation,
restart retains the phase and expired claims cannot write results.
Network/real Provider cancellation and native UI remain separate environment gates.

## Evidence

- RED: steering applied revision 2 before the blocked Media result existed.
  After preserving the boundary, the old result was incorrectly rejected with
  `EXECUTION_INTENT_CHANGED`; recording known outcomes now retains original facts
  while still validating current scope and the original Job/Command/Run binding.
- Pause/steer × normal/lost handoff acknowledgements pass with one generation,
  one artifact and one invocation. Manual pause does not start a new model round.
- RED: pausing immediately before dispatch repeatedly claimed the unstarted node.
  A declined reconciliation now removes only the dispatch-phase marker, preserving
  the checkpoint identity. Resume performs the original operation once.
- SQLite close/reopen, two runs, two tenants, cancellation, unregistered/wrong
  phases, wake deadlines and stale Fence checks pass. A completed receipt does not
  unlock ordinary pending work while paused.
- Direct gate: 8 passed in 15.34s. Workflow, Media, step controls/tools/media,
  execution-intent and uncertain-outcome regression: 163 passed in 131.74s.
- No existing test assertion was relaxed. One new fixture initially looked for
  Job ID in model context; corrected to the actual Artifact ID contract.
