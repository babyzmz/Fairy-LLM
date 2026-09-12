# Stability recovery: final gate and rollback

## Current handoff

The 2026-09-12 pass wrote Phase 6–8 changes before regressions, as requested.
Implementation and the unified deterministic gate are complete. Whole-plan
acceptance remains open for real provider/hardware and full native interaction
journeys. Computer Use was stopped by the user's physical Escape key; the isolated
dev lifecycle was closed without further UI input. Historical green results alone
do not cover this pass.
See `../acceptance/recovery-final-implementation-pass.md` and the original audit.

## Implementation boundaries

Engine 4 persists routing, objective boundaries, model rounds, tools, joins,
verification and finalization. It uses Kernel workers (global 4, normal 2/deep 4);
domain waits do not occupy parent workers. Approval and uncertain side-effect
recovery remain fail closed. Dependent mutation objectives have distinct file
plan generations bound to tenant/run/revision/objective. Prior commands, approvals,
evidence and checkpoints remain facts; only the final reply promotes the scratch
version. New plans inherit run limits without resetting cumulative run usage.
Operation objectives need successful current-objective command receipts.

`assistant/engine_version.py` controls **new** run defaults. It is now 4 after the
new-engine full Core regression. Stored runs retain their engine; keep the v3 adapter
while any v3 run is nonterminal. Do not compare legacy engine identity against a
mutable default constant. Do not rewrite stored engine versions.

Schema 0062 adds file-plan objective identity. Before opening the real database,
close Fairy or use SQLite's backup API for a consistent backup. Check foreign keys
and reopen after migration. PostgreSQL offline SQL is not live database evidence.
Downgrade refuses nonzero-objective history; it never deletes it. An older binary
alone is not a database rollback: restore a matching backup or migration.
The downgrade guard uses transaction-local `row_security=off` so restricted
migration roles fail rather than inspect a silently filtered subset; it does not
grant bypass privileges ([PostgreSQL row security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)).

The Rust host owns audio focus across WebViews, including realtime startup.
WebViews subscribe before reading its snapshot and ignore old sequence values.
Realtime interrupts ordinary/ambient speech; suppressed automatic reply events
are consumed rather than queued for later playback. Late PCM, ready callbacks and
old stop handles cannot control a new playback. Ordinary UI waits for host focus.

One five-second host maintenance tick releases an idle ordinary voice worker after
five minutes. Active consumers, queued requests and preparation/install protect it;
new requests reset the deadline. Health snapshots/maintenance never start Python.
An active MiniCPM session is not stopped by this voice maintenance. The default
Realtime keep-warm period is five minutes **after standby**, using its existing
suspend/unload protocol; explicit user preferences are retained. Hardware must
confirm actual VRAM release, microphone behaviour and active-session protection.

Cold starts, runtime self-tests, realtime wake/recovery share a bounded startup
reservation. Initial local admission refreshes hardware budget; CUDA/OS and
runtime readiness remain memory authorities. No fixed 16-GiB rejection was added.
CosyVoice CUDA OOM is reported separately. Startup reservations follow actual
`session_state` events and release on failure/EOF, not fictitious event names.

External Tauri window DDA exclusions use reference-counted ownership and an HWND
generation marker. Failure/last-owner shutdown attempts to restore prior attributes;
stale owners cannot clear replacement ownership. Restore errors are reported and
must be examined in native testing. Composition HWNDs retain their destruction
lifecycle. Full local glass remains default; remote compatibility is manual.
The input glass owns a disabled paint-only child HWND below the WebView child;
it must not attach a second composition target to the WebView2-owned parent.
It has no input or business authority, and is destroyed on its compositor thread.

## Diagnostics and development maintenance

Negotiated `transport.diagnostics` runs on the local bounded read lane, not Cloud.
It reports SQL/RPC counts and elapsed time, active kernel nodes/runs and resident
browser sessions/tabs. No prompts, SQL text/parameters, credentials, document
content or workspace paths are recorded. Main-window-only
`runtime_diagnostics_get` combines this with voice state, queue/consumer counts,
idle unload counts and model-start reservations. This creates no network port or
new scheduler. Counters describe software lifecycle, not measured GPU allocations.

Run `scripts/recovery_gate.py` with the core venv Python for ordered local suites.
`--only` selects named gates; others remain `not_run`. JSON/Markdown is updated after
each command including failure/interruption. It performs no automatic installation,
Docker, release, live provider, hardware capture or upload. External gates remain
`not_verified`; a green deterministic report is not whole-product acceptance.
Rust test threads are serialized so the three real-Core startup fixtures do not
compete with one another inside the unchanged three-second readiness gate.

For a consistent backup and migration rehearsal only, use:
`core/.venv/Scripts/python.exe scripts/recovery_database_probe.py --source <absolute-core.db> --output <new-backup-directory>`.
The source is read-only, backup uses SQLite's backup API, and migrations run on a
separate copy. Existing output directories are rejected. Keep the resulting backup
until the real application migration and acceptance have completed.

`scripts/development-cache.ps1` inventories fixed rebuildable paths only. It deletes
nothing by default. `-Clean -Only <allowlisted-name>` explicitly requests deletion
with confirmation; `-WhatIf` previews it. Junctions/symlinks and potentially related
active developer processes block deletion. Close developer tools first and do not
race another build. Models, environments, runtimes, user files, databases and
backups are excluded. Deleted cache does not go to the recycle bin; rebuild it.
Do not delete Rust `target` before running native-worker acceptance from that path.

## Cross-module journeys

| Journey | Deterministic evidence | Additional real evidence |
|---|---|---|
| Modify one file twice; second approval across restart | Objective plans/checkpoints/migrations | Provider + native worker/Git |
| Analyze then modify; steer with outstanding calls | Intent and workflow suites | Auto/Manual provider transcript |
| Two chats, hidden Inspector, background tasks | Desktop/Playwright suites | Two WebViews and reload/replay |
| Ordinary reply → realtime → stop → ordinary reply | Audio focus/generation tests | Microphone, speakers, five-minute VRAM release |
| Native/SVG, hide/show, local/remote capture | Lifecycle unit contracts | Twenty transitions and actual recording |
| Idle Core, browser capacity, cache growth | Counter/resource tests | Idle SQL delta, process/page counts, cache inventory |

## Closure

TypeScript → Vitest → Ruff → Core → Capabilities → Cloud local contracts → Voice →
Rust/Clippy → Playwright; then native-worker, real provider, available WSL/database
and Windows/WebView2 journeys. Recheck the engine-default switch itself. Record
missing environments as not verified. Close only test-owned processes through their
lifecycle, never unrelated user applications. Stop native automation on competing
user input. Earlier residual Phase 5 query-efficiency and native gates remain in
the original audit; this pass does not retroactively mark them complete.
