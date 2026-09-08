# Steering while model budget approval is pending

Engine-4 task updates may supersede an unstarted budget request, but must never
reuse its approval for the revised route. Validate the Turn-bound budget command,
Task/Conversation/Scope, approval record and the waiting routing node. Unrelated
Changeset/tool approvals and running commands are not covered by this gate.

Close a pending old approval as rejected by Core steering; preserve an already
recorded approval decision and cancel its unstarted command. Close the old Trace
approval, reset route/budget binding, resume the Turn and append the new immutable
plan in one transaction. Reclassify the new source and require a fresh budget
approval if its cost policy still requires one. No execution model call may occur
between these approvals without the new authorization.

Tests: real SQLite/Core, two conversations, pending and already-approved-but-queued
cases, two distinct budget commands/approvals, late old approval rejection/replay,
new context source and final response uniqueness. Scripted classifier/model output
does not represent a real paid-provider acceptance or a PostgreSQL race test.

## Evidence

- Both pending and approved-but-not-dispatched budget updates first failed at the
  blanket approval gate. The owned engine-4 routing gate now atomically closes the
  old request and clears its binding before compiling the new plan.
- Four decision/restart combinations create distinct budget approvals. Two
  classifier calls occur before the new approval; the execution model is called
  only after the new approval is accepted. Final reply count remains one and the
  other conversation stays unchanged.
- Replaying an old approved budget card initially raised a stale-binding error.
  A decided, cancelled/rejected command with matching Task/Conversation/Scope now
  returns its historical decision with `resume_requested=false`. A different
  decision still fails; a mismatched or pending binding is not accepted.
- Budget/tool steering, model routing, controls and media steering gate:
  **43 passed (60.09s)**. Changed Ruff and `git diff --check` pass.
- This is not an engine-default switch or Changeset steering acceptance.
