# Event Ledger Identity and Cursor Recovery

## Scope and completion rule

This specification covers durable event identity, bounded history, desktop cursor
persistence, and local/cloud resume behavior. Completion requires a restarted client
to recover every available user/developer event without suppressing a new ledger or
projecting duplicates.

## Observable behavior and invariants

- Each tenant event ledger owns one durable UUID `ledger_id`. Reopening or backing up
  the same database preserves it; a newly created database receives a different ID.
- `events.state` returns `ledger_id`, `oldest_cursor`, and `latest_cursor` for the same
  user/developer event visibility used by desktop delivery.
- `events.list(cursor, limit)` returns an ordered, bounded history page and a monotonic
  `next_cursor`. It never returns internal events.
- Desktop persists one structured checkpoint containing `source_id`, `ledger_id`, and
  `cursor`; the legacy bare `fairy.events.cursor` value is never trusted.
- A source change, ledger change, cursor ahead of latest, or cursor older than retained
  history resets to `oldest_cursor - 1` (or zero for an empty ledger).
- Desktop drains history up to the state watermark before starting live delivery.
  Events created during the drain are then received by the subscription.
- Checkpoints advance only after the corresponding event has been projected.
- Duplicate and out-of-order events at or below the current cursor are ignored.
- Local JSON-RPC polling and Cloud REST/SSE expose the same state and list contracts.

## State ownership

| State | Owner | Scope key | Lifetime | Recovery rule |
| --- | --- | --- | --- | --- |
| Ledger identity | Database `event_ledgers` row | tenant ID | Database lifetime | Lazily create once, preserve in backup |
| Event cursor bounds | Domain event ledger | tenant + visibility | Query snapshot | Recompute from durable rows |
| Desktop checkpoint | Device local storage | source + ledger | App restarts | Reconcile against `events.state` |
| Backfill cursor | Event delivery loop | source + ledger | One subscription | Persist after projection |
| Live cursor | Transport subscription | source + ledger | Connected stream | Resume from last projected event |

## Risks

The primary regression is a bare local cursor from database A suppressing all events
from database B. Other risks are retention gaps, a restored older backup, a race
between history and live events, cross-tenant cursor interleaving in PostgreSQL, and
an infinite loop when a history page makes no progress. All reset decisions are
therefore based on ledger identity and visible cursor bounds, and history paging must
fail explicitly if the server violates monotonic progress.

## Acceptance scenarios

| Scenario | Required result | Forbidden result | Environment |
| --- | --- | --- | --- |
| Reopen same SQLite DB | Same ledger ID and cursor bounds | New identity on restart | Real SQLite close/reopen |
| Open a new DB with stale device cursor | New identity; replay starts at available oldest event | All events suppressed | Core + desktop unit integration |
| Restore older DB with same checkpoint ahead | Reset and replay from oldest | Wait forever above latest | Desktop event loop test |
| Retention removed early rows | Start at `oldest - 1` | Request deleted history forever | Desktop event loop test |
| Events arrive during backfill | Historical events then new live events, once each | Gap or duplicate | Controlled async client |
| Two cloud users | Independent ledger IDs and visible bounds | Cross-user state/cursor leak | In-memory and PostgreSQL/RLS |
| Restart desktop | Structured checkpoint resumes after last projected event | Replays entire ledger or skips gap | Browser storage test |
| Aborted subscription | Backfill/live loop exits and performs no late projection | Background updates after unmount | Desktop async test |

## Automation boundaries

SQLite tests use a real file and close/reopen it. Desktop tests use real localStorage
and a deterministic async event client; they mock only the transport, not checkpoint
or replay logic. Cloud unit tests prove identity isolation in the in-memory adapter.
PostgreSQL migration, RLS, and restart behavior require Docker PostgreSQL and are
reported separately; static SQL generation cannot be called a PostgreSQL pass.

## Pre-fix evidence

Before this change, `workspacePreferences.readEventCursor()` loaded the global bare
key `fairy.events.cursor`, and `workspaceModel` immediately subscribed from that
number. Core exposed only `events.subscribe` and no ledger identity or history API.
Therefore a stored cursor greater than the latest cursor of a replaced database made
the client wait forever while dropping every new event below that stale value.

## Verification evidence

- Core ledger, service, and JSON-RPC regression suite: `33 passed`.
- Cloud sync, HTTP contract, migration, and PostgreSQL schema contract suite:
  `52 passed`.
- Cloud full non-integration suite: `115 passed`, `1 skipped`, `27 deselected`.
- Desktop Vitest suite: `57 files passed`, `235 tests passed`; TypeScript check passed.
- Generated OpenAPI and TypeScript contracts are stable across regeneration.
- Ruff checks and the repository boundary check pass for the changed surfaces.
- Docker Desktop's Linux engine was unavailable during this task. The PostgreSQL
  integration scenario and cleanup coverage are present, but no real PostgreSQL pass
  is claimed from static migration checks.
