# Steering and unstarted approvals

An updated task revision must not inherit or remain blocked by an unstarted tool
from the superseded plan. Pending approval is closed with a public supersession
reason (not represented as a user click). Already approved but not executed calls
are cancelled without changing the historical approval decision. Running effects
remain under the existing outcome-reconciliation gate.

Task, Invocation, Command, Approval and Trace changes are atomic with the plan
revision. Only bindings owned by the Turn/current plan may change. Old approved or
rejected decisions cannot resurrect the old node, including after Core restart.
The new model context retains a bounded rejected-tool result, not an execution
claim. No new notification/write is authorized by steering to explain/review.

Tests use real Core, SQLite, approval records and two conversations, with a recording
executor standing in for OS notifications. Include pending and approved-but-queued
cases, late decisions, replay and restart. Budget/Changeset approvals require their
own matching domain gates; do not infer safety for these from tool-only tests.

## Evidence (2026-09-09 local continuation)

- RED: both pending and already-approved tool cases were rejected at the blanket
  Workflow approval guard, so the user could not replace an unstarted operation.
- The new engine-4 gate accepts only owned tool approval nodes. Unstarted calls
  are rejected in the plan transaction; pending approvals record `core:steering`,
  while historical approvals remain unchanged. Command transitions retain their
  existing lease/fence checks and cannot seize a live executor's lease.
- Eight cases cover pending/approved, one/two serial calls and restart before the
  next model round. All preserve the other conversation, reject late approval or
  harmlessly replay an existing decision, retain one plan revision and make zero
  executor calls. The next model receives an explicit not-executed tool result.
- Approval, steering, deferred media and Trace regressions: **33 passed (53.04s)**.
  Changed Python Ruff checks and `git diff --check` pass. An initial test command
  referenced a nonexistent Trace test file and ran no tests; the corrected gate
  above uses the actual Trace test files.
- SQLite/Core and scripted Provider evidence only; PostgreSQL competing writers,
  real models and native UI are not covered by these results. Budget/Changeset
  steering and the engine default switch remain separate outstanding work.
