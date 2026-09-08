# Changeset application failure must settle its Assistant owner

## Observed execution boundary

Core records an approval, starts the apply Command, and may fail while applying
files or refreshing their index. The domain transaction records Changeset/Task
failure and rethrows. The service's normal approval callback consequently does not
refresh the proposal receipt or wake its waiting Assistant Workflow. The join
currently treats failed Tasks as ready rather than failed. Reproduce this before
implementation; code inspection alone is not the acceptance evidence.

## Required behavior

- Distinguish user rejection from an approved operation that failed. Preserve the
  original approval decision and apply Command outcome. Do not convert failure
  to a successful reply, to user cancellation, or to another automatic file apply.
- Reconcile the original invocation, public trace and Assistant/Workflow terminal
  state regardless of whether the apply failure comes before or after the tool
  receipt is published. Publication and notification must keep their existing
  locked domain read/late lookup ordering.
- Only the owner Task/Turn/Changeset/approval may be reconciled. The same event
  cannot wake another conversation or rewrite another proposal. A replay or app
  restart must not repeat the write.
- Use a bounded public failure code and no raw exception text. A failure may
  happen after some filesystem work, so do not claim that no files changed. The
  public summary must ask for outcome inspection before another attempt.
- Chat must show this specific outcome-inspection guidance instead of offering a
  blind Retry response button. No new controls or layout changes are introduced;
  an explicit new user request after inspecting the files remains available.
- Kernel-owned nodes settle through their original claim or an authoritative
  persisted domain failure projection. Do not fabricate or borrow a command lease.

## Acceptance

Inject an apply error before I/O and an error after successful file I/O.
Exercise early and normal approval timing, engines 3/4, and a distinct unaffected
conversation. Assert approved history, failed domain and workflow state, no waiting
trace, no false final message, exactly one attempted apply and no restart replay.
Use temporary workspaces/databases and the real service/Command/Approval/Workflow
chain with a scripted model. This does not stand in for WSL/native/real Provider
acceptance. Actual desktop data is not migrated or edited by these tests.

## Evidence

- RED: engine 4, an approved apply failing after its proposal receipt became
  visible, stayed `waiting_for_tool` instead of failed (9.55s test). A separate
  lost-notification/reopen case reproduced the same stranded state (10.06s).
- Core now wakes only the matching failed Changeset owner. The node reads and
  reconciles the original domain receipt, then settles as failure through its
  existing Workflow claim. Legacy engine 3 also stops before another model round.
- Startup recovery discovers owned waiting Workflows whose Task/Changeset failed,
  including a notification lost during shutdown. It never reapplies files.
- A new RED retry test showed the previous API accepted blind retry after file
  application failure. Both Core retry and the chat retry affordance now require
  outcome inspection; explicit new user instructions remain possible.
- Approval/restart/Steering/repository/uncertain-operation regression: **59 passed
  (91.48s)**. Core application/service/execution-domain regression: **47 passed
  (29.10s)**. TypeScript passed; ChatWorkspace **17 tests passed (6.39s)**. Ruff
  and diff whitespace checks passed.
- The existing early-approval test retained all success/rejection cases and added
  before-write/after-write failures plus lost-notification/reopen cases for engines
  3 and 4. The UI added a specific failure-message/no-blind-retry assertion.
- Native UI, real filesystem/sandbox faults, real Provider and PostgreSQL are
  still required in the final joint acceptance; none is claimed from these tests.
