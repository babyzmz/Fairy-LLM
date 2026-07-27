# Realtime Companion Beta Phase 7 Implementation Plan

> **Design:** `docs/superpowers/specs/2026-07-28-realtime-companion-beta-phase-7-design.md`
>
> **Authority:** the frozen Phase 0 contracts and the user-approved Realtime
> Companion Beta final design.

## Goal

Persist bounded generic Companion Session digests, route memory through
policy-classified proposals into Hermes Claims, carry only approved public
state across Context Rotation, govern the real local runtime under GPU
pressure, recover one local Sidecar crash and quarantine the next, and prove
long-session behavior with deterministic and real-duration gates.

## Non-goals

This phase does not add raw-media retention, cloud memory sync, CPU fallback,
automatic Cloud switching, Companion approval authority, new external-write
tools, installer packaging, paid-provider claims, or Phase 8 distribution.

## Task 1: Persist generic Companion Session digests

**Files**

- Modify `core/src/fairy_core/contracts/realtime.py`
- Modify `core/src/fairy_core/contracts/methods.py`
- Modify `core/src/fairy_core/realtime/models.py`
- Modify `core/src/fairy_core/realtime/ports.py`
- Modify `core/src/fairy_core/realtime/repository.py`
- Modify `core/src/fairy_core/realtime/application.py`
- Modify `core/src/fairy_core/application/realtime_service.py`
- Modify `core/src/fairy_core/storage/realtime_schema.py`
- Modify serialization/export and generated contracts
- Add focused Core tests

**Implementation**

1. Add strict digest create/get/list contracts, activity and summary fields,
   transcript source range, source digest, policy version, and proposal links.
2. Persist digests with tenant/session/conversation foreign keys, unique
   `(session_id, request_id)`, revision checks, and bounded text/list checks.
3. Require a terminal Session and stable public transcript evidence from the
   same Session before creation.
4. Add a deterministic summarizer port that fails closed when no supported
   facts exist.
5. Project legacy Game Memory records as `activity=game`; stop treating new
   compatibility records as implicitly accepted.
6. Emit bounded Ledger events without transcript bodies.

**Tests**

- schema and contract bounds;
- idempotent replay and mismatched conflict;
- stable-only evidence and cross-session rejection;
- terminal-state requirement;
- restart/list ordering and tenant isolation;
- legacy game projection and delete compatibility.

**Commit**

`feat(core): persist companion session digests`

## Task 2: Promote governed Realtime memory into Hermes

**Files**

- Modify the Core Realtime model, repository, schema, application, contracts,
  and service files from Task 1
- Add a narrow Core Realtime-to-Hermes bridge
- Modify Memory repository/application helpers only where required for
  USER_PROFILE and DEVICE_LOCAL Claim writes
- Add focused Memory and Realtime tests

**Implementation**

1. Add durable proposal kind, evidence, target namespace, decision reason,
   sensitivity, status, revision, and optional Claim link.
2. Generate proposals only from stable user captions covered by the digest.
3. Auto-promote only explicit clean game progress, next goal, and explicit
   remember-this preferences; reject secrets and conflicts.
4. Keep inferred and sensitive proposals pending.
5. Add list/accept/reject methods; require explicit confirmation and expected
   revision for decisions.
6. Atomically emit a source Ledger event, create/revise a Hermes Claim,
   transition the proposal, and link the Claim.
7. Keep Companion and pet out of the decision path; preserve retention,
   forget, conflict, and projection behavior.

**Tests**

- policy matrix for every proposal kind and sensitivity;
- no inference/sensitive auto-promotion;
- acceptance/rejection idempotency and revision conflicts;
- Hermes Claim namespace, authority, source event, and revision;
- memory-disabled and scanner-blocked behavior;
- no fake Task or active-chat mutation;
- restart, tenant, Conversation, and Session isolation.

**Commit**

`feat(core): govern realtime memory proposals`

## Task 3: Carry bounded Core context across rotations

**Files**

- Modify `desktop/src-tauri/crates/realtime-worker/src/protocol.rs`
- Modify `desktop/src-tauri/crates/realtime-worker/src/runtime.rs`
- Modify local/cloud Backend rotation payloads
- Modify `desktop/src-tauri/src/realtime_worker.rs`
- Modify `desktop/src-tauri/src/realtime_coordinator.rs`
- Add focused Rust tests

**Implementation**

1. Add a strict `RealtimeContextCarryover` protocol model.
2. Build it natively from stable-caption digest, current goal/activity,
   Core-verified short memories, unfinished Assistance identity, and Persona
   digest.
3. Bind carryover to current Session/Segment/Epoch and the next Epoch.
4. Validate every bound and reject raw/interim/media/hidden fields by schema.
5. Send carryover through acknowledged rotation and commit only the matching
   acknowledgement.
6. Preserve the same rules for privacy resume, profile/window change,
   standby wake, context budget, task change, and manual rotation.

**Tests**

- bound and identity validation;
- stale acknowledgement fencing;
- exactly approved field projection;
- unfinished Assistance continuity;
- no media, credential, tool payload, hidden state, or raw transcript;
- 20 sequential rotations without buffer or identity leakage.

**Commit**

`feat(desktop): preserve bounded realtime context`

## Task 4: Enforce the Native GPU Resource Governor

**Files**

- Create `desktop/src-tauri/src/realtime_resource_governor.rs`
- Modify Native hardware/DXGI sampling integration
- Modify `desktop/src-tauri/src/realtime_worker.rs`
- Modify `desktop/src-tauri/src/realtime_coordinator.rs`
- Modify Worker protocol/main/runtime
- Modify `desktop/src-tauri/crates/realtime-worker/src/frame_gate.rs`
- Add focused Rust tests

**Implementation**

1. Implement the pure Normal/Pressure/High/Critical/DeviceRemoved state
   machine with fixed thresholds, minimum dwell, and five-sample recovery.
2. Sample only while a local Realtime Segment is active; ordinary startup
   remains cold.
3. Send an explicit policy command when the effective level changes.
4. Apply 1 FPS, 0.5 FPS, and 0.25 FPS inspection intervals, plus background
   and user-initiated analysis gates.
5. On Critical, pause/drain local media and expose an explicit Cloud option.
6. On DeviceRemoved, terminate and clean the Segment with a stable public
   error.
7. Never select CPU or Cloud automatically and never run an unbounded retry.

**Tests**

- threshold, dwell, hysteresis, and composite signal behavior;
- FrameGate interval changes and boost caps;
- critical pause/recovery and device-removed cleanup;
- cold-start behavior;
- no CPU/Cloud fallback;
- bounded public resource projection.

**Commit**

`feat(desktop): govern realtime gpu pressure`

## Task 5: Recover and quarantine the real local Sidecar path

**Files**

- Integrate or replace `desktop/src-tauri/src/omni_runtime_manager.rs`
- Modify `desktop/src-tauri/src/realtime_worker.rs`
- Modify `desktop/src-tauri/src/realtime_coordinator.rs`
- Modify Worker protocol/runtime/local Backend failure projection
- Modify local readiness and persisted quarantine state
- Add focused Rust tests

**Implementation**

1. Classify local Sidecar exit/protocol disconnect separately from Worker,
   capture, provider, and user-stop failures.
2. Preserve a credential-free bounded recovery envelope in Native.
3. End the failed Segment, drain media, revalidate readiness and Persona, and
   start one automatic recovery Segment.
4. Mark interrupted context without replaying media or hidden Backend state.
5. Quarantine on the second crash or recovery-start failure and feed the
   persisted state into local readiness/backend resolution.
6. Add explicit Verify-and-retry clearing; model/app version change may also
   clear a stale quarantine.
7. Keep Fairy main, Core, and Companion alive and reconstruct projection after
   Companion reload.

**Tests**

- pre/post-candidate crash classification;
- one restart with new Segment/Epoch 1;
- second failure and failed restart quarantine;
- manual verification and version-change clearing;
- Persona and identity preservation;
- no raw media/credential retained;
- main process and Companion projection survive.

**Commit**

`feat(desktop): quarantine failing realtime sidecars`

## Task 6: Present digest and proposal state

**Files**

- Regenerate `desktop/src/core/generated/api.d.ts`
- Regenerate `desktop/src/core/generated/rpcMethods.ts`
- Modify `desktop/src/core/contracts.ts`
- Modify `desktop/src/core/client.ts`
- Modify Realtime/Memory query modules
- Modify the main-window Realtime or Memory settings surface
- Modify `desktop/src/realtime/RealtimeCompanion.tsx`
- Modify scoped styles and tests

**Implementation**

1. Add typed digest/proposal client methods and scoped invalidation.
2. Show digest, automatically saved facts, pending review, and rejection in
   the main window.
3. Keep Accept/Reject in the main window with confirmation and revision.
4. Show only bounded saved/review-available state and Open-main-chat in
   Companion.
5. Restore current state after Companion reload and clear it across Sessions.
6. Preserve keyboard, narrow-window, Reduced Motion, and no-overflow behavior.

**Tests**

- typed transport and invalidation;
- accept/reject/forget lifecycle;
- no approval control or sensitive detail in Companion;
- reload and Session isolation;
- accessibility and 620-pixel Companion / narrow main layouts.

**Commit**

`feat(desktop): present realtime memory review`

## Task 7: Add deterministic and real-duration soak gates

**Files**

- Add Rust deterministic soak tests and fixtures
- Add `scripts/test-realtime-companion-soak.ps1`
- Add script/unit tests where practical
- Update local developer documentation

**Implementation**

1. Build a virtual-time four-hour scenario with 20+ rotations, 10+
   pause/resume cycles, five+ window changes, two Sidecar crashes, every
   resource level, and a Companion reload.
2. Assert monotonic Segment/Epoch/sequence values and bounded buffers.
3. Assert exactly one automatic restart, explicit quarantine, and no
   CPU/Cloud fallback.
4. Assert terminal digest/proposal creation and no identity/source leakage.
5. Add a fail-closed Windows wall-clock harness requiring eligible GPU, model,
   runtime, and explicit duration of at least four hours.
6. Record sanitized counters and process cleanup; never record media or
   transcript content.

**Tests**

- deterministic soak repeatability;
- prerequisite and duration validation;
- cancellation and cleanup;
- sanitized report schema;
- no ordinary-startup worker preheat.

**Commit**

`test(realtime): add long-session soak gates`

## Task 8: Certify Phase 7

**Files**

- Add `docs/acceptance/realtime-companion-beta-phase-7.md`
- Modify tests only for verified defects discovered during certification

**Verification**

1. Run focused and complete Core tests plus Ruff.
2. Run focused Rust tests, workspace tests, and strict Clippy.
3. Run TypeScript, focused and complete Vitest, and focused and complete
   Playwright.
4. Run the deterministic four-hour-equivalent soak.
5. Run the real four-hour local soak only on eligible hardware/model; retain
   truthful evidence or an explicit blocked gate.
6. Run one controlled Tauri dev WebView2 check for proposal authority,
   resource state, one crash recovery, quarantine, and Companion reload
   without competing with user input.
7. Stop all controlled processes and remove transient artifacts.

**Commit**

`test(realtime): certify memory and stability`

## Completion

Phase 7 is complete only after the implementation commits are present, all
non-environment-dependent gates pass, the acceptance record matches observed
evidence, the real four-hour eligible-hardware gate has passed for Beta
release, the worktree contains no Phase 7 changes, and no controlled process
or transient artifact remains.
