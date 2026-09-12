# File planning across dependent objectives

Status (2026-09-12): implemented; deterministic migration/recovery tests and the
opt-in real Rust/Git two-plan gate passed. Real providers/PostgreSQL/WSL remain
separate unverified gates. Original design below is retained.

## Required behavior and ownership

One Turn may request analysis, one change, further inspection and another change.
The existing file-plan uniqueness is per Workflow revision, and completed-plan
context suppresses another planning call. This cannot be solved by reusing old file
hashes or resetting a plan in place.

- Bind each new Execution Plan to its creation objective index (default 0 for old
  records), in addition to Task generation and Workflow Run/revision. Keep previous
  manifests and step outcomes immutable as history. A read objective may prepare
  a plan for the immediately following mutation phase; planning does not grant
  permission to apply it.
- A plan becomes obsolete for new execution only after a later, certified mutation
  objective consumed it. Intervening read objectives do not make an analysis plan
  obsolete before it can be applied. Derive this boundary from the active immutable
  interpretation and owned completed progress, never a model-supplied index.
- A subsequent plan receives a new Task generation and objective identity. Never
  replace a plan with running, failed, uncertain or unapplied effects. Any remaining
  project checkpoint is explicitly deferred to the whole Task's eventual review,
  not represented as an already executed checkpoint.
- Keep Workspace/Version, Scope, approval and complete-file-batch checks. A new
  Changeset has a distinct governed command/approval identity even for identical
  arguments. No automatic replay of an earlier approved proposal.
- Task/Run budgets remain cumulative. Creating a new file plan must not reset the
  Workflow model/tool/time budget or allow a second live Turn for the conversation.

## Checkpoint and finalization review

- Inspect scratch checkpoint idempotency: the current key is Task-wide. If a later
  plan changes the same candidate Version, its checkpoint must not reuse the earlier
  successful result. Preserve the old key for first-generation recovery; later
  generations need their own identity.
- Verify non-runnable scratch file output (for example a README) can finish without
  a Preview requirement while still making its real checkpoint and promotion.
  Do not mark the file-plan checkpoint complete merely to satisfy finalization.
- Intermediate objective completion can commit a candidate but cannot promote the
  active scratch Version or create an additional final assistant Message.

## Migration and acceptance

Add SQLite/PostgreSQL incremental schema with an objective range check, preserved
Task generation uniqueness and objective-scoped Workflow uniqueness. Old records
retain index 0 and prior recovery keys. Refuse partial schema or lossy downgrade.
Use temporary databases and real managed file operations, with scripted models:

1. Read A, change A, read its changed content, plan/change A again, then verify;
   both approvals required, two plan generations, source directory unchanged.
2. Repeat with another conversation and tenant; no plan, permission or receipt
   crossing. Steering preserves old plans but invalidates unstarted old work.
3. Close/reopen during either approval and between objectives; no repeated file
   application, no reused checkpoint, one final Message and one promotion.
4. Plain-file scratch output works without starting a Preview process. Multi-plan
   scratch output's last Git checkpoint includes the last actual file contents.
5. Unknown/failed writes and incomplete validation block the dependent objective.

Real Provider, PostgreSQL/WSL and native desktop remain separate final gates. This
increment does not add scheduling, Cloud migration or another resource scheduler.
