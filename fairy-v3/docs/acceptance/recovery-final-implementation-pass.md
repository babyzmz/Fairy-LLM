# Recovery final implementation pass — 2026-09-12

The user requested implementation of the remaining Phase 6–8 features before
running intermediate tests or regressions. This overrides the earlier per-change
test execution order, not the final acceptance requirements. Implementation was
written first; unified regressions subsequently found and corrected integration
failures described below. Historical results do not validate new changes.

## Implementation checklist

Implementation is written for the items below. The new-run default is now engine 4;
its full Core regression passed. Stored turns retain their original engine.
Implementation checkboxes do not certify the separate hardware/provider gates.
Operational details and native journey matrix:
`../operations/stability-recovery-final-gate.md`.

- [x] Phase 6: objective-scoped file plans, distinct approvals/checkpoints and
  intermediate completion without premature scratch promotion.
- [x] Phase 6: audit objective outcomes, steering, uncertain effects, cumulative
  budgets, global concurrency and engine-version migration/default selection.
- [x] Phase 7: shared audio ownership, realtime preemption, generation-safe
  consumers, idle five-minute model release and lifecycle cleanup.
- [x] Phase 7: model reservation/failure accounting and DDA ownership across
  native/SVG transitions; full local quality and manual remote compatibility.
- [x] Phase 8: bounded non-sensitive diagnostics and cross-module journey report.
- [x] Phase 8: targeted rebuildable-cache inventory/cleanup and recovery docs.
- [x] Final structural checks and automated regression.
- [ ] Real provider/hardware and full native interaction gates (see limitations).

## Acceptance and limitations

Use the original recovery matrix plus workflow-objective-file-plans.md. Persisted
state must be tested with two scopes, restart, stale completion, cancellation and
failure. Audio ownership must cover two clients, starting/stopping realtime,
queued speech, late callbacks and idle release without unloading active models.
DDA needs real Windows/WebView2, shape switching and capture evidence. Scripted
providers and offline PostgreSQL migration SQL are not real-provider/database
acceptance. Native automation must stop competing with user input.

Do not migrate the live desktop database before its consistent-backup gate.
The default-engine switch was itself tested with the full new-engine Core suite.
Preserve compatibility for existing nonterminal runs.
Do not delete models, runtime environments, user files or active build outputs.
Do not publish to GitHub, start Docker or build release artifacts.

## Unified verification evidence — this pass only

| Gate | Evidence |
|---|---|
| TypeScript | `node node_modules/typescript/bin/tsc --noEmit`: passed |
| Desktop unit tests | 112 files / 668 tests passed, 24.46 s |
| Ruff | Core, Capabilities, Cloud, Voice source/tests and both recovery Python scripts passed |
| Core, previous default 3 | 1346 passed / 4 explicit opt-in skips, 811.09 s |
| Core, default 4, final | 1368 passed / 4 explicit opt-in skips, 805.85 s; report `.tmp/recovery-gate/core-v4-final/report.json` |
| Capabilities | 120 passed, 3.09 s, using its own venv |
| Cloud local contracts | 138 passed, 21.86 s; no live PostgreSQL/S3 claim |
| Voice Worker | 19 passed, 1.24 s; no actual CUDA model loading |
| Real Rust/Git + Edge | 10 passed, 31.96 s; two file plans/final checkpoint and 20 Browser session switches; not WSL/Tauri GPU |
| Playwright | 85/85 passed in one controlled Vite lifecycle; event-delivery p95 threshold remains 100 ms |
| SQLite backup/reopen | Consistent backup + separate migration copy passed integrity/FK, row counts and engine identity; original DB untouched |
| Native host audio child processes | Actual no-model Python children reaped on malformed handshake, idle eligibility and shutdown; simulated elapsed idle time, not a five-minute VRAM measurement |
| Native composition | Occupied-parent target reproduced `0x88980800`; independent paint-only child passes 20 rebinds; full visual/input retest still required |
| Rust, final | Full workspace/all-targets passed; desktop host 331 passed / 1 opt-in model-download test ignored; Realtime 69 passed; integration crates passed; live GLM test explicitly ignored |
| Clippy / formatting / boundaries | Workspace/all-targets `-D warnings`, `cargo fmt --all -- --check`, `git diff --check` and `scripts/check_boundaries.py` passed |

The first default-4 Core run had 17 failures. Five were old engine assumptions;
four legacy finalization cases explicitly retain engine 3 while the v4 real-node
suite checks new recovery. Actual bugs were not masked by changing assertions:

- Durable tool context lacked sealed evidence receipts, causing missing citations
  and repeated Realtime Assistance calls. Reconstructed context now includes those
  receipts before the bounded body, including across model-node/restart boundaries.
- Invalid arguments in one candidate discarded valid siblings. Each offered invalid
  call is now persisted rejected with a hash-only payload and generic public error;
  valid siblings retain order and normal policy/approval. Reserved values are not saved.
- Prepared calls dropped sanitized public intent. The node checkpoint now preserves
  it separately from executable arguments and still revalidates tool/scope identity.
- Verification retries used a misleading invalid-tool trace label. They now retain
  the existing “Finalizing durable result” public state.

## Native joint findings and scope

A restricted-shell launch did not create a visible WebView2. Its process tree was
closed, then an approved unsandboxed Tauri **dev** launch used only
`.tmp/native-recovery-20260912`. The main window displayed CORE READY. No credentials
were copied and ordinary/Realtime model workers were not started.

Creating a scratch conversation reproduced a pet-binding failure. Host-generated
UUID string request IDs violated the Bridge's integer-client-ID contract. All pet
host calls now use one integer-ID envelope; the Bridge still assigns unique
generation/sequence wire IDs. A real Core/Bridge test binds two scratch chats with
the actual preference and conversation responses. Public UI errors expose only a
bounded uppercase code, never raw native details.

Input glass also logged `DCOMPOSITION_ERROR_WINDOW_ALREADY_COMPOSED`. A real hidden
HWND with its parent target occupied reproduces that exact error. The renderer now
owns a disabled, non-activating child behind WebView2, leaving WebView2's target and
input ownership alone. Repeated same-size frames do not issue SetWindowPos. The
child is destroyed with its compositor generation; 20 rebinds cover scale changes.
This is not permission to claim pixel quality or hit-testing passed without native
retest. UI automation stopped when the user began interacting with settings.
The post-fix dev launch reached the native executable; the user then pressed the
physical Escape key to stop Computer Use. No further UI automation was attempted.
The controlled Tauri/Vite lifecycle was closed. A subsequent read-only process
inventory found no project-scoped Node, Cargo, Fairy, Core or Voice worker process.
No post-fix screenshot or input/visual acceptance is claimed.

SQLite source backup SHA-256:
`081ddf899555c12d888b06f1271c931b1044f95f1f5d385f32b1c7869fe2370c`.
The backup retained 169 messages, 37 turns, 11 file plans, 93 invocations and one
Workflow Run. No nonterminal turn existed in this device's source; other devices'
stored legacy turns still require compatibility, so their adapters are retained.

## Existing tests changed and why

- `cloud/tests/test_deployment_contract.py`: schema 0062 offline upgrade/downgrade
  and lossy-history refusal coverage; `test_postgres_contract.py`: expected revision.
- `core/tests/assistant/test_approval_resume.py`: run the runner-exit race on both
  engines, delaying the actual tool node for v4 instead of its old model wrapper.
- `test_contracts.py`: expected new default and minimal initial graph.
- `test_durable_turn_worker.py`: an injected pre-wrapper command remains explicitly
  legacy; reopen tests the current host finishing the original engine.
- `test_evidence_completion.py`: receipt recovery under bounded large-content projection.
- `test_model_routing.py`: assert real v4 routing/model/verify/final nodes and final
  Turn/status output; unchanged message uniqueness and public routing trace assertions.
- `test_prepared_tool_execution.py`: test-created invocations carry the actual v4
  Run identity; scope/revision validation was not weakened.
- `test_step_workflow.py`: legacy creation is explicit; new default on reopen is not.
- `test_workflow_controls.py`: clarification must prevent both legacy and v4 execution.
- `test_workflow_finalization.py`: fixed legacy-graph fixture selects engine 3;
  real v4 finalization and lost-receipt coverage remains in `test_step_workflow.py`.
- `core/tests/execution/test_plan_generations.py`, `test_plan_revision_binding.py`:
  bind explicit governed test intent before testing downstream plan mutation.
- `core/tests/workspace/test_project_tools.py`: explicit create/change fixture intent;
  add useful failure diagnostics, not weaker safety/result assertions.
- `desktop/src/realtime/RealtimeCompanion.test.tsx`: specific public resource/focus errors.
- `desktop/src/voice/nativeVoice.test.ts`: late PCM/ready callbacks cannot revive old playback.
- `desktop/src/app/petChatBinding.test.ts`: bounded code reporting and sensitive-detail exclusion.
- `desktop/e2e/release.spec.ts`: slash permissions route through Core; original
  performance thresholds unchanged. `voice.spec.ts`: host focus preemption recovery.
- `desktop/e2e/support/coreFixture.ts`: actual native event-push, audio-focus, pet
  binding and slash-command contracts. `globalSetup.ts`: cleanup on setup failure.
- Rust existing tests in preferences, voice/realtime and native backend: five-minute
  default, shared focus/startup ownership, shutdown/idle reaping, child composition
  source contract. New native resource tests complement, not replace, these checks.

Rust's three real-Core tests launch separate processes; running their startup timer
alongside concurrent heavy suites exceeded 3 s once. The same unchanged budget
passes with test threads serialized. The gate runner now serializes Rust tests to
avoid treating parallel test-process startup as single-Core readiness evidence.

## Not certified by these results

Real Auto/Manual providers, real WSL sandbox, live PostgreSQL/S3, actual microphone
and speakers, five-minute wall-clock GPU release, and local/remote recording and
twenty native shape transitions still require their specified environments.
The user-interrupted native UI journey is partial, not a full pass. No release,
Docker build, remote upload or cache deletion was performed. Cache inventory found
26.62 GiB of rebuildable Rust target files on D:, but models/runtimes were excluded.

## Local commit handoff

- `cdddb2e47`: Phase 6 objective workflows, default engine 4, migration/recovery and
  bounded Core diagnostics foundation, with Core/Cloud regressions.
- `298a2e022`: independent pet host request-envelope and bounded public-error fix.
- `77e91591a`: independent input composition ownership fix and native rebind test.
- `fc0d3f8e2`: Phase 7 audio/native resource lifecycle and frontend/Voice regressions.
- The Phase 8 gate/docs commit follows these on `codex/fairy-stability-recovery`;
  no remote upload is part of this pass.

Feature implementation is complete for the remaining Phase 6–8 checklist; whole-plan
acceptance remains open for the explicitly unverified external/native journeys.
