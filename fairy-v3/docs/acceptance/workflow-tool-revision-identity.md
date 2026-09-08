# Tool identity across explicit Workflow revisions

The current Tool Invocation argument-hash unique constraint and Command key cover
the entire Turn. An explicit steering request to repeat a previous read, file plan
or unapplied proposal is therefore rejected as a duplicate. Media routing also
treats any historical completed generation as completion of the new request.

Keep canonical argument hashes unchanged and store an explicit trusted Workflow
Run/plan-revision binding on each Invocation. Engine 3/legacy invocations keep
their existing first-revision behavior; engine 4 resolves the revision from its
claimed node/current Run, not model arguments. Duplicate arguments remain blocked
inside one revision. Original receipts/commands are immutable evidence; an explicit
new revision can create a fresh call with fresh approval and the same arguments.

The Command idempotency key includes the new revision. Propagate that governed
command identity through the project executor so its domain Changeset key does
not accidentally replay the old rejected proposal. Resume continues to use the
original invocation/command identity, never a newly generated key. Apply already
started or uncertain side effects must settle before revision changes. Media
completion checks use only the active revision while retaining old artifacts.

Schema/migration: replace the Turn-wide argument unique constraint with a
Turn/revision constraint; add a positive revision and nullable legacy Run binding.
Backfill engine-4 provenance from persisted tool nodes, with the current revision
only for undispatched/in-flight legacy checkpoint records. Preserve every row,
FK and canonical argument hash. Matching SQLite upgrade/reopen and PostgreSQL
offline up/down gates are required; a lossy downgrade must refuse rather than
delete history. The actual desktop database remains untouched and has the
consistency recovery point documented in workflow-file-plan-revisions.md.

Tests: same-arguments new revision, same-revision loop suppression, rejected or
approved-but-unapplied old proposal requiring fresh approval, restart and replay,
two tenants/conversations, old read evidence retained, new media generation,
single original side effect after recovery. Public interfaces and permission
profiles do not expand. Real providers/PostgreSQL remain separate acceptance.

## Implementation and observed regressions

- RED: the same-file-plan Steering scenario failed on the old Turn-wide argument
  unique constraint, including while trying to save its duplicate-rejection record.
- Invocation canonical hashes remain unchanged. Run/revision are persisted and
  checked against the claimed tool/join node and current owner before dispatch.
  Argument and provider-call identities are unique within a revision; one revision
  cannot reuse a call ID. The first revision retains original Command keys for
  pending-command compatibility. Subsequent revisions get distinct keys.
- Project Changeset identity carries the governed Command ID for revised plans.
  Previously approved but unapplied proposals retain history and never grant a
  replacement proposal implicit approval.
- Duplicate calls within a revision now yield a bounded model correction with the
  recorded result, without inserting a row that violates uniqueness or executing
  again. Model arguments cannot set the revision.
- SQLite upgrade removes the obsolete explicit provider-call index as well as the
  old unique constraints. Generic legacy initialization must not recreate it.
  Populated node provenance and in-flight checkpoints are covered by upgrade and
  two reopens. Partial/ambiguous history fails without deleting rows. A terminal
  old invocation without a node and with a later active revision is ambiguous,
  not silently assigned to that new revision.
- Media output quota remains one attempt per kind per revision. A prior active
  operation blocks overlap; only explicit Steering after settlement can request
  another output. Read-only Steering can describe prior artifacts, whereas a new
  generation intent cannot count them as its completion. Both ordinary and lost
  handoff cases preserve the original job and create distinct new artifacts.
- Targeted gates: 9 Changeset Steering tests, 32 repository/model/migration tests,
  27 Media/real-tool/prepared-dispatch tests, and 15 final revision/Steering checks
  passed. These are overlapping suites, not additive test counts. Cloud offline
  migration/deployment contracts: 43 passed (3.45s).
- Broader gate: `pytest tests/assistant tests/workflow tests/media
  tests/test_sqlite_core.py tests/execution/test_plan_generations.py
  tests/execution/test_plan_revision_binding.py -q --tb=short`: **506 passed,
  434.25s**. Changed-area Ruff and `git diff --check` passed. No actual Provider,
  PostgreSQL, WSL, or native UI was started for this gate.
- Existing test changes: add same-arguments/restart cases and revision assertions
  to Changeset Steering, add explicitly requested repeat generation to Media
  Steering, and advance the Cloud migration head to 0060. Existing safety
  assertions are retained. The actual desktop database is not opened for writes.
