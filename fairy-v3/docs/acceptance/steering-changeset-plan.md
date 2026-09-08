# Changeset steering and file-plan revision

A task update must distinguish an unapplied file proposal from an operation that
has started changing the Workspace. Engine-4 steering can supersede only an owned
awaiting-approval Changeset whose apply command has not started. A running apply
must first reach a known outcome; no rollback or second apply is inferred.

Required atomic facts: old pending approval is closed by Core steering (or an
already-approved historical decision is preserved); the apply command cannot run;
the Changeset remains a rejected/superseded historical proposal; its completed
tool invocation reports that no files were applied. The old Trace and file-plan
work are terminal without claiming success. Completed facts and evidence remain.

The new workflow revision must not be forced to implement the old file manifest.
For an explain/review update, finalization must not auto-install, run validation,
start Preview or checkpoint files under the old plan. Workflow budgets remain in
force; an obsolete domain plan must not break a legitimate read-only continuation.
Further explicitly authorized file work needs a distinct durable plan revision,
with the old manifest and steps retained, not silently overwritten or reused.

Tests start with a real project Workspace, read evidence, execution.plan and a
pending Changeset. Cover pending/approved-but-not-applied, two conversations,
unchanged source/managed files, one final reply, bounded proposal receipt, old
approval replay, and refusal to supersede a running apply. SQLite schema changes,
if required for plan revision, need consistency backup and migration/reopen gates;
real PostgreSQL/native/Provider results remain separate.

## Implementation and evidence (2026-09-09)

- File plans now bind to the trusted engine-4 owner/revision. Same-revision
  manifests remain immutable/idempotent; a settled older revision gets a new ID
  and generation. Running batches cannot be superseded. Completed steps stay
  completed; the unexecuted proposal batch fails with `EXECUTION_INTENT_CHANGED`,
  pending steps skip and the old plan cancels.
- Steering atomically closes only an owned unstarted apply command and proposal.
  It preserves any existing approval decision, updates the original tool receipt,
  and closes both tool and approval Trace steps. Approved-card replay returns
  historical state without resuming the new task.
- Obsolete file plans no longer hide `execution.plan`, consume domain budget or
  force their old delivery contract. Workflow budgets remain authoritative.
  Automatic finalization checks the current execution intent before entering
  install/test/Preview machinery; conflicting delivery requirements cannot be
  silently reported as completed.
- RED cases covered approval steering, old-card replay, leftover approval Trace
  and unavailable revised planning. Final targeted suite: **55 passed (70.35 s)**,
  including two conversations, pending/approved boundaries, reopen, refusal of
  an unresolved apply, exact source/managed file assertions, a second independent
  plan/approval and one final reply. Changed-file Ruff and diff checks passed.
- Intermediate complete Assistant/Workflow run: 453 passed, 1 failed (358.16 s).
  Its delivery/intent conflict was corrected and the complete routing suite passed
  in the 55-test gate. A final full gate remains due after the next fixes.

## Independently discovered follow-up: early approval race

Approvals are visible as soon as the domain proposal commits, before its Assistant
tool receipt necessarily commits. Approving at this early boundary can apply the
files while the later receipt/Trace still says waiting. A join must also wait for
an APPLYING Changeset, not infer completion from Task=executing. The revised-plan
test above deliberately waits for the durable approval-wait node to isolate plan
supersession; this does not close the early-click race. Next: deterministic early
approve/reject and in-progress-apply barriers, receipt reconciliation and a fresh
turn lookup after domain settlement. Do not claim Phase 6 complete before that.

Two older `test_project_closure` executor fixtures also omit their interpreted
intent prerequisite; their tool calls are correctly withheld. Update those
downstream fixtures using the existing explicit test-intent helper, not weaker
production policy. No real Provider, PostgreSQL or native UI gate was run here.
