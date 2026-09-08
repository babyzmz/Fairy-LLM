# Workflow terminal failure projection

All Kernel terminal failure paths must settle the owning Assistant Turn, Task and
Trace in the same transaction, not only deadline maintenance or engine-4 adapter
exceptions. This includes exhausted retries, exhausted deferred waits and an
interrupted final attempt. Failed Runs must not retain live sibling leases or
dispatchable nodes. Other Runs/conversations remain unchanged.

Reuse the versioned owner failure adapter and the existing Command authority
rules. A failed Run does not prove a Provider or external side effect has stopped;
do not seize a live Command lease or replay uncertain operations. Rollback of the
domain projection must roll back the Kernel transition. Startup repair remains a
compatibility recovery path, not the normal way to finish a chat.

Acceptance: real Core/SQLite with engines 3 and 4, two conversations, injected
adapter outcomes, bounded attempts, and no real Provider calls. Check terminal
nodes/attempts, immediate matching Turn/Task error, stale Fence rejection and
rollback. PostgreSQL, hardware and real Provider acceptance remain separate.

## Targeted evidence

- Ten RED cases (engines 3/4) showed terminal failed Runs with still-created
  Assistant Turns: retry/defer exhaustion, abandonment, generic adapter failure
  and expired final-lease reclamation.
- The Kernel now invokes the existing versioned domain handler in the committing
  transaction. Final abandonment and lease expiration use the same sibling-node
  and attempt invalidation as explicit failure; only the failing node stays failed.
- Reclamation checks that its selected attempt is still running, has the same
  Fence and remains expired before changing any node. Attempts already settled by
  a sibling failure are not processed a second time.
- Targeted gate: **14 passed (14.66s)**, including sibling stale-Fence rejection,
  two isolated Runs and injected domain projection rollback. Earlier combined
  Workflow/deadline/Assistant gate: **78 passed (58.21s)** before final lease cases.
- Final combined `tests/workflow tests/assistant`: **419 passed (317.25s)**.
  Changed Ruff and whitespace checks pass. No real Provider, PostgreSQL, WSL or
  native WebView acceptance was run in this gate.
