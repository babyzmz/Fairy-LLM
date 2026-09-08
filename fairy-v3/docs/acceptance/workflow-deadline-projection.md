# Workflow deadline and domain convergence

Kernel maintenance must settle an expired Run and its owning domain projection in
the same transaction, including when no node can be claimed or a renewal discovers
the deadline. Assistant Turn, Task and public Trace become terminal without a new
Provider/tool call. A Run deadline invalidates its node Fence, but does not confer
ownership of live Command leases or claim external operations physically stopped.

Use versioned owner-domain callbacks rather than a second scheduler. Existing
terminal Runs whose Assistant projection was not settled by older code are repaired
in bounded startup batches. Callback failure rolls back the whole maintenance
transaction; persisted failure is not allowed to hide an uncommitted projection.

Verification: two conversations; legacy/default and opt-in engines; idle claim and
renewal; callback rollback; close/reopen and replay without duplicate terminal
events. Deadline selection must be database-filtered and bounded; pending overdue
rows cannot be dispatched before the next maintenance batch. Real Provider stop,
Cloud multi-process ownership and native UI remain separate gates.

## Evidence

- RED: all four idle/renewal × engine 3/4 cases left the Turn `created` after its
  Run failed. Both old-core restart cases also retained the stale nonterminal Turn.
- Kernel claim/renewal and step-adapter entry now invoke a versioned domain handler
  within the maintenance transaction. Startup repair reads at most 64 failed
  bindings per batch and repeated startup emits no duplicate terminal event.
- Injected projection failure rolls back both domain and Kernel state. Independent
  conversations stay unchanged; no model or tool request is issued.
- RED: a 65-run backlog was processed without a bound; after bounding selection,
  renewal could extend a later overdue Run. Maintenance now prioritizes its renewing
  Run, and the renewal SQL independently rejects expired deadlines, including when
  maintenance skips a locked row. Claim SQL also excludes all overdue rows.
- Deadline selection uses database time expressions and batches of 64; PostgreSQL
  row locks use SKIP LOCKED. The skipped-row test injects that boundary in SQLite;
  it is not a substitute for real PostgreSQL concurrency acceptance.
- Direct deadline/recovery/rollback/batch gates: 11 passed in 13.00s. An earlier
  Workflow and step regression passed 84 tests in 75.48s; the final full Assistant
  and Workflow regression passed: 397 tests in 312.26s (5m12s). Ruff and
  `git diff --check` passed. No existing assertion was relaxed.
