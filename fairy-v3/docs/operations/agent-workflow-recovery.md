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

## Verification

Run `uv run --project core fairy-agent-eval`. Preserve `report.json` and `report.md` with the build
evidence. A missing real Provider, PostgreSQL/S3, WSL Sandbox, or native WebView2 environment must
be recorded as `unverified`; deterministic fixture success cannot substitute for it.
