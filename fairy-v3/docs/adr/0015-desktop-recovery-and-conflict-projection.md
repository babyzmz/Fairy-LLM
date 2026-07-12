# ADR 0015: Separate Core Recovery, Residency, and Version Conflicts

- Status: Accepted
- Date: 2026-07-12

## Context

The desktop previously collapsed all connectivity and concurrency failures into
plain error text. A local-only project, a synced project with cloud connectivity
loss, a stopped local Core, a worker interruption, and a stale Active Version
revision require different user decisions.

## Decision

The Context Bar reports project residency independently from Core health and
execution target. `LOCAL ONLY` means the project has no cloud synchronization;
`SYNCED` describes residency, not current network reachability.

Core health failure renders a dedicated offline state with an explicit retry
that resets all workspace queries. Typed operation failures retain their stable
error code. `WORKER_INTERRUPTED` is recoverable through the same retry path.

A `VERSION_CONFLICT` from Active Version acceptance states that the candidate
was preserved separately and the Active Version was not overwritten. No desktop
path automatically merges or promotes it. A permission revision conflict is a
different optimistic-concurrency context: the desktop reloads the latest Core
settings, clears the version interpretation, and asks the user to review and
retry.

## Consequences

- Local work is not mislabeled as cloud synchronization.
- Offline and interrupted states have an explicit recovery action.
- Candidate versions survive multi-device conflicts without silent promotion.
- Shared error codes are interpreted with operation context instead of a global
  string mapping.
