# File plans across Workflow revisions

## Problem and bounded design

`core_execution_plans` currently has one unique row per tenant/task. This preserves
neither a new manifest after steering nor a safe read-only continuation: cancelling
the old plan makes its budget methods reject later model calls, while leaving it
active can force the model and finalization hook to complete superseded file work.

Keep file planning as a domain projection, not a second scheduler. Add a positive
plan generation and nullable Workflow Run/plan-revision binding. Legacy records
retain generation 1 and null bindings. New engine-4 plans bind to the active
Workflow revision. Task lookup returns only the newest generation; lookup by plan
ID continues to return the original manifest/steps. A different manifest within
one revision remains a conflict. A new authorized revision creates a new plan ID,
not an overwrite of old files, counters or steps.

Supersession is permitted only after old operations have known outcomes. Completed
steps/evidence remain. The old plan becomes cancelled; pending steps are skipped,
and a waiting-for-proposal implementation step ends with the public reason
`EXECUTION_INTENT_CHANGED`, not success. Active file application or validation
cannot be relabelled as unstarted work.

Read-only continuations do not need a new empty file plan. Their budget is still
charged to the authoritative Workflow Run; a terminal old file plan is not charged
or revived. Completion/Preview automation must inspect the latest execution intent
and must not demand old edits or automatically install/test/run after an explain,
review or explicit no-execution update.

## Migration and compatibility gates

- SQLite rebuild replaces the task-only unique constraint with task/generation
  uniqueness and adds Workflow provenance. Preserve all existing plan/step rows,
  enforce foreign keys, reopen twice and verify no duplicate migration.
- PostgreSQL gets a matching incremental Alembic migration. A downgrade with
  multiple generations must refuse data loss; restore a matching consistency
  backup rather than delete historical plans. Offline SQL is not a live PG test.
- Optional response metadata is additive. Existing desktop plan rendering and
  engine-3 legacy records retain their established shape and behavior.
- Cover two tasks/tenants, same-revision idempotency/conflicts, new revisions,
  completed-fact retention, stale application guards, read-only finalization and
  unchanged files. All new actions still pass intent, Scope, Policy and Approval.

## Pre-migration recovery point

Before any production schema edits, the actual default desktop database was
backed up using SQLite's backup API (including committed WAL contents), not raw
file copying. Source: `%APPDATA%/com.fairy.desktop.v3/core.db`.

Local ignored backup: `.tmp/db-before-plan-revisions-20260909/core.db` (10,162,176
bytes); `quick_check=ok`, zero foreign-key violations. SHA-256:
`081ddf899555c12d888b06f1271c931b1044f95f1f5d385f32b1c7869fe2370c`.
The source was opened read-only; no original database migration or app startup has
occurred. This backup is not staged or uploaded and contains private application
data; never include it in source commits or test output.

## Storage verification (2026-09-09)

- RED: the new generation constructor failed first; after the additive model/store
  change, a populated legacy schema failed on its missing generation column.
- SQLite now rebuilds the old unique constraint atomically, preserves child steps
  and restores FK enforcement. New/reopened databases cover latest lookup, original
  manifests, separate generation/revision uniqueness, invalid revision FKs and
  cross-tenant rejection. Execution planning and SQLite regression: 20 passed
  (12.09 s).
- Cloud migration 0059 adds matching constraints. Downgrade refuses any versioned
  provenance before dropping columns, rather than deleting history. Cloud
  deployment/offline migration suite: 42 passed (3.43 s), using its existing venv.
  Core's venv lacks Cloud/FastAPI; no packages were installed to work around that.
- Changed-file Ruff and diff checks pass. No live PostgreSQL or original desktop
  database migration was performed. Domain supersession, revised manifests and
  read-only completion are the next implementation step, not covered by these
  storage-only results.
