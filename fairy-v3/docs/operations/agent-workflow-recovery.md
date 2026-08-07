# Agent Workflow Recovery Runbook

## Startup and shutdown

- Closing Fairy to the tray keeps the local Core and its Workflow scheduler alive.
- Explicit app exit stops new claims, requests pause for local Runs, releases or abandons active
  attempts safely, and leaves persisted work for the next launch.
- Cloud Runs remain Cloud-owned. Fairy never migrates an existing Run between local and Cloud.
- On restart, expired attempts are reclaimed with a higher fence. A late process cannot settle the
  new owner's node.

## Upgrade guard

The release removes the old `core_assistant_turn_work` queue. Before schema cleanup, SQLite and
PostgreSQL query for any Assistant Turn that is non-terminal and either lacks a Workflow Run or has
an engine version below 2. If found, startup/migration stops with the blocking Turn IDs (SQLite) or
an explicit Cloud migration error.

Do not delete those rows or bypass the guard. Start the prior Fairy release against a backup,
finish or cancel each Turn, verify it is terminal, then retry the upgrade. Terminal legacy Turns
remain readable after migration.

## Recovery decisions

- Safe read with expired lease: retry within the node's bounded attempt count.
- Command completed and result persisted: project the existing result; do not execute again.
- Write/execute outcome uncertain: pause and require an operator recovery decision.
- Waiting approval: no worker is held; resume only through the existing Approval authority.
- Failed or cancelled Run: terminal; user retry creates new durable work.
- Corrupt/missing plan or cross-scope identity: fail closed and retain evidence for diagnosis.

## Local scheduled instructions

- Closing to the tray keeps schedule sweeps active. Explicit exit stops new occurrence claims;
  startup performs the next compensation sweep before normal polling resumes.
- A one-shot schedule missed while Fairy was stopped dispatches once. A recurring schedule keeps
  only its latest missed instant and records the earlier count on the occurrence.
- The occurrence identity is `(tenant_id, schedule_id, scheduled_for)`. If restart diagnostics
  show more than one Message, Turn, or Workflow Run for that identity, stop the current Core and
  preserve the database and Ledger before retrying.
- A busy conversation or a still-running previous occurrence may retain one latest pending
  occurrence. Waiting approval counts as active and must not be bypassed by a later trigger.
- A stale claim may not settle after its lease expires because the replacement claim owns a newer
  fence. Do not manually clear a claim without retaining its attempt and fence evidence.
- `context_attention` pauses future occurrences when Provider, credential, permission, Workspace,
  Version, or execution-target snapshots no longer match. Repair the named binding, review the
  retained instruction, then explicitly resume; never substitute a new binding automatically.
- Three consecutive failed occurrences pause a recurring schedule. Diagnose the public error and
  underlying Turn trace before resuming. Pausing or cancelling the Schedule never cancels an
  already dispatched Turn.

Windows notifications contain only a public summary and scoped navigation IDs. A missing Toast is
not execution evidence: inspect the Schedule card, background-task projection, occurrence, Turn,
Workflow Run, and Ledger in that order. Toast activation requires an installed Windows app identity
and is verified separately from renderer tests.

## Verification

Run `uv run --project core fairy-agent-eval`. Preserve `report.json` and `report.md` with the build
evidence. The deterministic report includes schedule dispatch boundaries, restart idempotency,
lease fencing, overlap coalescing, failure pause, DST, and offline compensation. A missing real
Provider, PostgreSQL/S3, WSL Sandbox, Windows Toast activation, or native WebView2 environment must
be recorded as `unverified`; deterministic fixture success cannot substitute for it.
