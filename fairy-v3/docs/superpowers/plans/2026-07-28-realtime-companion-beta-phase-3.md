# Realtime Companion Beta Phase 3 Implementation Plan

> Execute in order. Keep each task independently reversible and commit with the
> specified Conventional Commit subject. Do not run Docker, release builds,
> paid provider sessions, model downloads, or claim CUDA/model inference.

## Task 1: Extract the Cloud backend

Files:

- replace `desktop/src-tauri/crates/realtime-worker/src/backend.rs` with
  `backend/mod.rs`
- add `backend/cloud.rs`
- modify `runtime.rs`, `provider.rs`, `transport.rs`, `lib.rs`, and focused
  tests

Steps:

1. Define the internal backend input/event/error/usage contract.
2. Move `ProviderSocket` ownership and provider output translation into
   `CloudLiveBackend`.
3. Make the shared runtime capture and route separate microphone,
   application-audio, and video inputs through `RealtimeBackend`.
4. Preserve current provider behavior, bounded queues, cancellation, usage,
   barge-in, and safe error codes.
5. Add injected Cloud adapter tests proving no provider is constructed for a
   Local launch.

Commit:

```text
refactor(realtime): isolate cloud backend
```

## Task 2: Connect the Local Omni backend

Files:

- add `desktop/src-tauri/crates/realtime-worker/src/backend/local_omni.rs`
- add a small reusable Omni client module/crate if needed
- modify the Worker `protocol.rs`, `main.rs`, desktop
  `realtime_worker.rs`, and Phase 2 Omni protocol/supervisor modules
- modify `desktop/native/omni-runtime` control/backend/media sources and tests

Steps:

1. Pass trusted managed runtime/model/manifest launch data from Tauri to the
   Worker without exposing it to React or diagnostics.
2. Launch Omni with a cleared environment, inherited process job, no window,
   strict paths, bounded stderr, and Phase 2 deadlines.
3. Perform hello, model load, context begin, media writes, commits, event
   translation, cancellation, context rotation, and stop.
4. Load the production backend from the verified manifest/model root in stdio
   mode. Contract and CPU profiles must remain unable to report ready.
5. Translate only bounded decisions and safe diagnostics to Worker events.
6. Add contract executable integration tests for local media/control and
   fail-closed readiness.

Commit:

```text
feat(realtime): connect local omni backend
```

## Task 3: Add the authoritative resolver

Files:

- add `desktop/src-tauri/src/realtime_backend_resolver.rs`
- modify `desktop_preferences.rs`, `local_model_control.rs`,
  `realtime_worker.rs`, `lib.rs`, and focused Rust tests
- update TypeScript transport types for the read-only resolution preview

Steps:

1. Model resolution inputs, exact result, and stable public unavailable reason.
2. Implement the Local, Cloud, and Auto truth table from the Phase 3 design.
3. Require current local readiness, cloud credential status, fallback policy,
   per-session upload consent, and compatible voice output.
4. Expose an authorized read-only preview command that starts nothing.
5. Rerun resolution during start and reject stale or renderer-forged backend
   selections.
6. Prove failed resolution starts no Worker, Omni process, capture, or socket.

Commit:

```text
feat(desktop): resolve realtime backends authoritatively
```

## Task 4: Enforce Backend Segments

Files:

- extend `desktop/src-tauri/src/realtime_coordinator.rs`
- modify `realtime_worker.rs`, `lib.rs`, and worker-event projection
- add focused Coordinator and Tauri command tests

Steps:

1. Generate Segment identity and ordinal inside Tauri.
2. Store backend, optional cloud provider, Persona digest, and creation reason
   in the active Segment state.
3. Validate every Worker event against Session/Segment/Epoch identity.
4. Represent backend failure as paused and action-required, never as an
   automatic fallback.
5. Add an explicit continuation command that reruns resolution and creates a
   new Segment while preserving only bounded public context.
6. Test user approval, Persona mismatch, stale events, repeated failures, and
   absence of silent switching.

Commit:

```text
feat(realtime): govern backend segments
```

## Task 5: Preserve Core session audit

Files:

- modify Core Realtime models, contracts, application mapping, and tests
- update TypeScript Realtime session types as needed

Steps:

1. Add the pinned Local MiniCPM endpoint to the historical session-audit
   provider enum without using it as an execution resolver.
2. Map it to the pinned local model identifier.
3. Preserve existing rows and cloud projections without a destructive schema
   migration.
4. Add domain, contract, repository, and service regression tests.

Commit:

```text
feat(core): audit local realtime sessions
```

## Task 6: Project resolved backend in the Companion UI

Files:

- modify `desktop/src/realtime/RealtimeCompanion.tsx`, styles, client/transport
  types, and component tests
- modify `desktop/e2e/realtime-readiness.spec.ts`

Steps:

1. Load the read-only resolution preview with preferences and capture sources.
2. Stop hard-coding `cloud_live`.
3. Display exact Local/Cloud backend identity and stable unavailable guidance.
4. Present distinct Local and Cloud privacy copy and explicit cloud-upload
   consent.
5. Start only the exact previewed resolution; refresh on stale resolution.
6. Render active backend from Coordinator/Worker state.
7. Test narrow layout, keyboard flow, reload projection, unavailable states,
   and no secret/path disclosure.

Commit:

```text
feat(desktop): project realtime backend choice
```

## Task 7: Certify Phase 3

Files:

- add `docs/acceptance/realtime-companion-beta-phase-3.md`
- add or modify focused boundary tests required by findings

Steps:

1. Run Omni source-lock, patch clean-apply, contract control/media, and CPU
   compatibility gates.
2. Run focused and complete Realtime Worker, Tauri, and Core tests.
3. Run TypeScript, complete Vitest, Rust format/lint/workspace tests, focused
   Playwright, and complete Playwright.
4. Run controlled native Tauri dev. Confirm ordinary startup/Settings starts no
   Omni, Voice, or CosyVoice process and resolver preview is side-effect free.
5. Record CUDA/model/live-provider gates as deferred when unavailable.
6. Remove native source/build caches, traces, screenshots, staged contract
   runtime, and all controlled processes.

Commit:

```text
test(realtime): certify backend abstraction
```

## Exit criteria

- Cloud and Local use one internal backend contract.
- Tauri, not React, resolves Auto.
- Local/Cloud switching is always explicit and creates a new Segment.
- Contract/CPU Omni artifacts remain fail-closed.
- No local path opens a provider socket and no cloud path starts Omni.
- Active backend identity and privacy scope are visible to the user.
- The tree is clean except for the preserved repository-level `CLAUDE.md`.
