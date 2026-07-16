# Durable Assistant Turn Worker Acceptance

## Observable behavior

- `assistant.turns.start` returns promptly after durably requesting work.
- A requested Turn continues after a Core process restart without another client request.
- One Turn has at most one active worker lease and produces at most one final Assistant message.
- Approval during a prior worker's shutdown schedules one newer revision and cannot be lost.
- Cancelled, failed, and completed Turns stop being claimable and never retain an active lease.
- Partial streamed text from an interrupted model attempt is reset before replacement text is projected.

## State ownership

| State | Owner | Scope key | Restart rule |
|---|---|---|---|
| Turn domain state | Assistant ledger | `tenant_id + turn_id` | reload unchanged |
| Work request | Assistant work queue | `tenant_id + turn_id + request_revision` | reclaim after lease expiry |
| Worker claim | Assistant work queue | `lease_owner + lease_fence` | stale owners cannot renew or release |
| Model/tool command | Command ledger | `tenant_id + command_run_id` | reclaim expired command lease |
| Stream projection | durable events | `turn_id + model_round + chunk_index` | reset before recovered model retry |

## Invariants

- `request_revision >= completed_revision`; pending means the inequality is strict.
- Repeated ordinary starts while work is pending reuse the same request revision.
- A new approval may force exactly one newer request revision while an older claim is active.
- Completing revision N never consumes a concurrently enqueued revision N+1.
- Every claim, heartbeat, release, and cancellation is fenced by tenant, owner, and lease fence.
- Assistant Command leases are 20 seconds, Turn leases are 30 seconds, and both renew every 5 seconds; a Command can never outlive a crashed Turn worker's claim.
- A worker that loses its lease cancels its local execution token and cannot settle queue state.
- Tenant A cannot see, claim, renew, cancel, or release Tenant B work.

## Acceptance scenarios

| Scenario | Required result | Forbidden result | Evidence |
|---|---|---|---|
| Duplicate start | one pending revision | duplicate worker/model call | SQLite repository test |
| Two workers claim | exactly one claim | concurrent claims | SQLite and PostgreSQL tests |
| Approval/finish race | revision N+1 remains pending after N releases | lost approval | scheduler test |
| Lease heartbeat | lost claim is abandoned and retried | consumed work or cancelled Turn | SQLite scheduler test |
| Process crash | expired claim is reclaimed and completes | permanent running/spinner | close/reopen SQLite test |
| Partial stream crash | reset event precedes replacement deltas | duplicated response text | recovery integration test |
| Cancellation | pending work and lease are fenced off | worker resumes cancelled Turn | scheduler test |
| Terminal replay | same terminal Turn is returned | new queue request | service test |
| Tenant isolation | only owning tenant can claim | cross-tenant execution | PostgreSQL integration test |

## Failure and timeout behavior

- A worker exception must settle the Turn through existing bounded failure handling, then release its work claim.
- Heartbeat failure cancels local work; an expired durable claim becomes reclaimable.
- Graceful service close interrupts active workers, abandons their leases, and waits without changing the Turn to user-cancelled or deleting pending newer revisions.
- Legacy running Turns without a durable work record are marked `WORKER_INTERRUPTED`; this compatibility path is explicit and finite.

## Test boundaries

- Scripted providers prove deterministic retries, message counts, and projection resets; they do not prove OpenRouter availability.
- SQLite tests use the real database, close and reopen it, and exercise separate repository instances.
- PostgreSQL claim/RLS tests require the Docker integration environment and must be reported separately if Docker is unavailable.
- Thread termination by OS kill is approximated by an expired persisted lease; graceful `close()` is not claimed as a crash.
