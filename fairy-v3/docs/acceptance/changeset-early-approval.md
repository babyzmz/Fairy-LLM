# Changeset approval before the tool receipt

A proposal/approval row is visible after the domain transaction, before the
Assistant tool necessarily commits its receipt. UI approval at that boundary
must not leave a stale waiting Trace, let a join finish while files are APPLYING,
or miss the Assistant wake-up because the invocation was still RUNNING when the
request began.

Use the persisted Changeset and its scoped Approval as the outcome authority.
Reconcile a stale proposal receipt in the invocation completion transaction; after
domain apply completes, re-resolve the owning invocation if it was unavailable at
request entry. Pending/applying outcomes still wait. Terminal receipts must report
the actual outcome without submitting a second apply. Preserve Command ownership,
Scope, approval policy, original IDs, and a unique final response.

Deterministic gates use Events around proposal return and Workspace application,
not timing guesses. Cover approve/reject before receipt, apply held open while the
receipt commits, two conversations, unchanged source files, exactly one managed
write, correct terminal Trace, and owned thread/process cleanup. Only temporary
workspaces and scripted models; no actual Provider/native/PG acceptance is implied.

## Verification (2026-09-09)

- All three Event-controlled cases failed before the fix: stale awaiting receipt,
  rejection left waiting, and a completed Run while application was held open.
- Invocation publication now locks its Changeset row (SQLite no-op write without
  changing timestamp; PostgreSQL `FOR UPDATE`) and checks task/conversation/
  Workspace/Version/Approval ownership. This serializes receipt publication with
  domain settlement, not physical file I/O. No apply is replayed.
- The approval endpoint resolves the owning invocation again after settlement;
  joins wait for APPLYING. Rejection settles the Turn/Trace as cancelled without
  borrowing a model lease: the model command already completed before its tool
  graph was dispatched. The legacy loop also checks rejection before another
  model round.
- Both engines 3 and 4 pass approve-before-receipt, reject-before-receipt and
  held-apply cases. Core application/service plus early approval and plan steering:
  **53 passed (55.94 s)**. Each approval writes at most once, has at most one final
  response, keeps the source/other conversation untouched, and leaves no waiting
  Trace. Owned threads close in `finally`.
- Changed-file Ruff/diff checks pass. No actual Provider, live PostgreSQL, native
  WebView2 or user database migration was run. Full-project gates remain pending.
