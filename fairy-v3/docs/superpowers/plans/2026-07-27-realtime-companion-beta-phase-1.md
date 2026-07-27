# Realtime Companion Beta Phase 1 Implementation Plan

> **Execution rule:** implement task-by-task with tests first, one reversible
> Conventional Commit per task, and preserve the untracked repository-root
> `CLAUDE.md`.

**Goal:** Deliver truthful Windows hardware qualification, governed MiniCPM-o
artifact management, a future Omni self-test boundary, and a Settings readiness
card without starting or claiming an unfinished local runtime.

**Architecture:** Tauri owns a `LocalReadinessService` composed of a read-only
Windows hardware probe, an `OmniModelManager`, and an injected runtime self-test
runner. The Phase 0 policy evaluator remains the single decision function.
React receives only a typed projection and invokes explicit model-management
commands.

**Tech stack:** Rust 2021, Tauri 2, Windows DXGI and CUDA Driver APIs, serde,
SHA-256, reqwest, React 19, TypeScript, TanStack Query, Vitest, Playwright.

## Global constraints

- Do not enable Realtime Beta or Local Beta by default.
- Do not add CPU fallback.
- Do not create a second desktop application or Python backend.
- Do not start Voice, Realtime, Omni, CUDA inference, or a model download on
  startup or Settings navigation.
- Do not include model bytes in Git or a desktop installer.
- Do not expose absolute model paths, raw handles, credentials, environment
  variables, or command lines to React.
- Do not use a real MiniCPM download in automated tests.
- All transfer fixtures are local and bounded.
- Phase 2 owns the real `fairy-omni-runtime`; Phase 1 must report
  `runtime_missing`.
- Run npm commands from `desktop`, Cargo commands from `desktop/src-tauri`, and
  project-wide searches from the Fairy project root.

---

## Task 1: Pin and harden the Omni model manifest

**Files**

- Modify: `desktop/src-tauri/src/omni_model_manifest.rs`
- Create: `desktop/src-tauri/resources/omni/minicpm-o-4.5.json`
- Create: `desktop/src-tauri/src/omni_model_catalog.rs`
- Modify: `desktop/src-tauri/src/lib.rs`
- Modify: `desktop/src-tauri/tauri.conf.json`

**Steps**

- [ ] Add failing tests for canonical `manifest_digest` verification, reviewed
  repository revision, exact runtime compatibility, exact LLM/vision/audio file
  set, Windows reserved names, alternate data streams, trailing dot/space,
  case-folded duplicates, non-HTTPS URLs, and size overflow.
- [ ] Query the official OpenBMB Hugging Face repository metadata at one exact
  commit. Record only official LFS object sizes and SHA-256 OIDs; do not invent
  hashes or follow `main`.
- [ ] Add canonical manifest serialization that excludes `manifest_digest` and
  hashes a deterministic field order.
- [ ] Add a catalog loader for the bundled resource. Fail closed when the
  resource digest or schema is invalid.
- [ ] Bundle the JSON manifest as a Tauri resource without bundling model
  artifacts.
- [ ] Run:
  `cargo test omni_model_manifest omni_model_catalog`
- [ ] Commit:
  `feat(realtime): pin omni model manifest`

---

## Task 2: Implement the Windows hardware probe

**Files**

- Modify: `desktop/src-tauri/src/hardware_capabilities.rs`
- Create: `desktop/src-tauri/src/hardware_probe.rs`
- Create: `desktop/src-tauri/src/hardware_probe/windows.rs`
- Modify: `desktop/src-tauri/Cargo.toml`
- Modify: `desktop/src-tauri/src/lib.rs`

**Steps**

- [ ] Add injected-probe tests for deterministic adapter selection, software
  adapter exclusion, NVIDIA vendor ID, 16 GiB physical VRAM, dynamic budget,
  AVX2, low-memory warning, CUDA failures, and exact LUID matching.
- [ ] Add Windows OS, x64, AVX2, and total-memory detection.
- [ ] Enumerate high-performance DXGI adapters, select the first NVIDIA
  hardware adapter, and query local video-memory budget/current usage.
- [ ] Dynamically load the CUDA driver API, call only initialization/version/
  enumeration/LUID functions, and release the library without creating a CUDA
  context.
- [ ] Return bounded diagnostics with no raw handles or paths.
- [ ] Implement a non-Windows fail-closed probe.
- [ ] Run:
  `cargo test hardware_capabilities hardware_probe`
- [ ] Commit:
  `feat(desktop): probe realtime local hardware`

---

## Task 3: Implement the model manager domain and filesystem store

**Files**

- Create: `desktop/src-tauri/src/omni_model_manager.rs`
- Create: `desktop/src-tauri/src/omni_model_store.rs`
- Modify: `desktop/src-tauri/src/lib.rs`

**Steps**

- [ ] Add tests for the install state machine, legal transitions, operation
  sequencing, retry, cancellation, and one-active-operation behavior.
- [ ] Add path-containment and no-symlink tests for staging, partial, version,
  and state paths under `%LOCALAPPDATA%\Fairy\models`.
- [ ] Add exact disk preflight:
  remaining artifact bytes + 2 GiB temporary allowance + 5 GiB post-install.
- [ ] Add streaming SHA-256 and exact-size verification with cancellation
  checkpoints.
- [ ] Add complete layout verification and same-volume atomic promotion.
- [ ] Persist bounded install state atomically; never persist absolute paths.
- [ ] Preserve resumable partials, remove corrupt final files, and never mark a
  partial layout Ready.
- [ ] Run:
  `cargo test omni_model_manager omni_model_store`
- [ ] Commit:
  `feat(desktop): manage omni model artifacts`

---

## Task 4: Add bounded HTTPS resume downloads

**Files**

- Create: `desktop/src-tauri/src/omni_model_download.rs`
- Modify: `desktop/src-tauri/src/omni_model_manager.rs`
- Modify: `desktop/src-tauri/Cargo.toml`

**Steps**

- [ ] Add local-fixture tests for fresh download, honored Range resume, ignored
  Range restart, HTTPS policy rejection, bounded redirect policy, cancellation,
  response timeout, server truncation, and manifest-size ceiling.
- [ ] Add a reqwest client with no cookies or credentials, bounded redirects,
  connection/read timeouts, and HTTPS-only production URLs.
- [ ] Stream directly into the staged `.partial` file while hashing and
  publishing bounded progress.
- [ ] Restart safely when the server ignores a valid Range request.
- [ ] Integrate sequential manifest-file installation. Parallel multi-gigabyte
  downloads are intentionally excluded from Beta.
- [ ] Run:
  `cargo test omni_model_download omni_model_manager`
- [ ] Commit:
  `feat(desktop): download omni model safely`

---

## Task 5: Define runtime self-test and local readiness service

**Files**

- Create: `desktop/src-tauri/src/omni_runtime_self_test.rs`
- Create: `desktop/src-tauri/src/local_readiness.rs`
- Modify: `desktop/src-tauri/src/hardware_capabilities.rs`
- Modify: `desktop/src-tauri/src/lib.rs`

**Steps**

- [ ] Add tests for runtime missing, strict response schema, protocol mismatch,
  digest mismatch, bounded output, timeout, crash, and failed self-test.
- [ ] Add a self-test runner that invokes only the bundled Omni path with the
  verified manifest/model directory and no credentials.
- [ ] Add a service that composes hardware, manifest, model, runtime, disk, and
  profile budget into the Phase 0 policy evaluator.
- [ ] Cache hardware facts briefly; explicit refresh bypasses the cache.
- [ ] Keep startup shallow: read install metadata/file presence only. Full hash
  and self-test run only after install, explicit verify, or a future Local
  start.
- [ ] Prove that Phase 1 production state returns `runtime_missing`, never
  Ready, when the Phase 2 binary is absent.
- [ ] Run:
  `cargo test omni_runtime_self_test local_readiness hardware_capabilities`
- [ ] Commit:
  `feat(realtime): evaluate local beta readiness`

---

## Task 6: Expose authorized Tauri commands and progress

**Files**

- Modify: `desktop/src-tauri/src/lib.rs`
- Modify: `desktop/src-tauri/src/realtime_worker.rs`
- Modify: `desktop/src-tauri/src/process_lifetime.rs`
- Modify: relevant Tauri capability files under `desktop/src-tauri/capabilities`

**Steps**

- [ ] Add command authorization tests for main/Settings read access, Settings
  mutation access, Companion read-only access, and Pet denial.
- [ ] Add typed commands:
  `realtime_local_readiness_get`, `omni_model_status`,
  `omni_model_install_start`, `omni_model_install_cancel`,
  `omni_model_verify`, and `omni_model_remove`.
- [ ] Add one managed service instance to `DesktopState`.
- [ ] Publish progress with a monotonic sequence and no absolute paths.
- [ ] Attach self-test children to existing process lifetime protection.
- [ ] Change Local start rejection to consume the readiness service. It remains
  blocked in Phase 1 because the runtime is missing.
- [ ] Run:
  `cargo test --workspace`
- [ ] Commit:
  `feat(desktop): expose local model readiness`

---

## Task 7: Build the Settings readiness card

**Files**

- Modify: `desktop/src/settings/client.ts`
- Create: `desktop/src/settings/RealtimeReadinessCard.tsx`
- Create: `desktop/src/settings/RealtimeReadinessCard.test.tsx`
- Modify: `desktop/src/settings/SettingsApp.tsx`
- Modify: `desktop/src/settings/settings-app.css`
- Modify: `desktop/src/settings/SettingsApp.test.tsx`

**Steps**

- [ ] Define exact TypeScript projections for hardware, CUDA, model, runtime,
  budget, warnings, progress, and overall status.
- [ ] Add Settings client calls and a Voice-category query that runs only after
  that category is visited.
- [ ] Render truthful states:
  unknown, unsupported, installable, downloading, partial, verifying, corrupt,
  runtime missing, self-test failed, temporarily unavailable, and ready.
- [ ] Add explicit refresh, install/resume, cancel, verify, and confirmed remove
  actions.
- [ ] Preserve Cloud settings when hardware is unsupported.
- [ ] Disable the Local backend option/start projection unless overall status is
  Ready.
- [ ] Respect Reduced Motion and keep progress/status accessible to keyboard and
  screen readers.
- [ ] Run:
  `npx tsc --noEmit`
  and focused Vitest for Settings/Realtime.
- [ ] Commit:
  `feat(desktop): show realtime local readiness`

---

## Task 8: Phase 1 regression and acceptance

**Files**

- Modify/Create: Settings and Realtime Playwright fixtures/specs under
  `desktop/e2e`
- Create: `docs/acceptance/realtime-companion-beta-phase-1.md`

**Steps**

- [ ] Add Playwright cases at 880x680 and 640x700 for unsupported, installable,
  downloading, runtime-missing, temporarily unavailable, and Cloud-available
  states.
- [ ] Prove Settings navigation starts no download or worker.
- [ ] Prove Local start stays disabled without the complete readiness report.
- [ ] Run:
  - `npx tsc --noEmit`
  - focused Desktop Vitest
  - complete Desktop Vitest
  - `cargo fmt --all -- --check`
  - `cargo clippy --workspace --all-targets -- -D warnings`
  - `cargo test --workspace`
  - focused Settings/Realtime Playwright
  - complete Playwright
- [ ] Perform a controlled Tauri dev check without competing with user input.
  Confirm the real adapter/VRAM presentation is truthful and that no Voice,
  Realtime, Omni, model, or unexpected CUDA process starts.
- [ ] Do not run a real model download. Do not claim CUDA qualification unless
  the actual probe returns it and the evidence is recorded.
- [ ] Stop the exact process tree, confirm ports/processes are clean, and remove
  `desktop/test-results` plus bounded temporary traces.
- [ ] Record passed, skipped, deferred, and hardware-specific gates in the
  acceptance document.
- [ ] Commit:
  `test(realtime): certify phase one readiness`

## Phase 1 completion gate

Phase 1 is complete only when:

- the current machine receives a truthful hardware report;
- model operations are production-shaped but tested with bounded fixtures;
- no partial/corrupt/missing-runtime state can become Ready;
- Settings exposes evidence and Cloud remains usable;
- Local start remains fail-closed until Phase 2;
- all required static, unit, integration, E2E, native lifecycle, and cleanup
  checks pass; and
- the worktree is clean except the preserved repository-root `CLAUDE.md`.
