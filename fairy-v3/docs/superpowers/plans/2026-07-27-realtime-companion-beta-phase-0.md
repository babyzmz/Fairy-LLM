# Realtime Companion Beta Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze and implement the inactive-safe contracts that let Fairy V3 evolve from a game-only cloud companion into one governed Local/Cloud Realtime Companion without enabling unfinished local inference.

**Architecture:** Keep Fairy Core authoritative for Persona and governed actions, move user-visible session ownership toward a Tauri coordinator, and split backend identity from cloud provider identity in the Rust worker. All new local capability, manifest, and coordinator types are fail-closed pure contracts in Phase 0; the existing cloud path remains functional until the final Phase 0 switch removes the hard-coded Worker Persona.

**Tech Stack:** Rust 2021, Tauri 2, serde, Pydantic v2, Python 3.13, React 19, TypeScript, Vitest, pytest, Cargo tests, Playwright.

## Global Constraints

- Product identity remains one Fairy Persona and one Realtime Companion.
- `Auto`, `Game`, and `Focus` are profiles; `Quiet`, `Standard`, and `Active` are interaction intensities.
- Local Beta remains disabled by default and may not claim readiness in Phase 0.
- Local Beta requires Windows x64, NVIDIA dedicated VRAM `>= 16 GiB`, AVX2, CUDA/DXGI adapter match, verified artifacts, passing self-test, and sufficient current budget.
- No CPU fallback may be labeled local Realtime Beta.
- A Backend Segment uses one backend; Local/Cloud switching requires explicit user action.
- MiniCPM, the Worker, the Pet, and React cannot execute tools.
- Microphone and application audio stay on distinct tracks.
- Raw audio, raw frames, interim captions, prompts, credentials, provider payloads, and hidden reasoning never enter logs or persistence.
- Stable public captions remain the only automatically persisted content.
- Local and Cloud receive the same Core-issued Persona digest.
- Do not run Docker, release builds, production packaging, model downloads, live providers, or GPU qualification in Phase 0.
- Preserve the untracked repository-root `CLAUDE.md`.
- Run Python and contract-generation commands from the Fairy project root,
  Cargo commands from `desktop/src-tauri`, and npm/Playwright commands from
  `desktop`.

---

## File structure

Phase 0 creates or changes these focused units:

- `docs/adr/0022-governed-realtime-companion.md` — replacement architecture decision.
- `desktop/src-tauri/crates/realtime-worker/src/backend.rs` — backend-neutral types and validation.
- `desktop/src-tauri/crates/realtime-worker/src/protocol.rs` — v2 control/event wire shapes.
- `desktop/src-tauri/crates/realtime-worker/src/main.rs` — consumes Core Persona Snapshot instead of a hard-coded prompt.
- `desktop/src-tauri/src/realtime_worker.rs` — Tauri v2 worker command adapter.
- `core/src/fairy_core/persona/realtime.py` — deterministic Persona Snapshot projector.
- `core/src/fairy_core/contracts/persona.py` — RPC request/response models.
- `core/src/fairy_core/application/realtime_service.py` — `realtime.persona.snapshot` handler.
- `desktop/src-tauri/src/hardware_capabilities.rs` — fail-closed hardware report model and policy evaluator.
- `desktop/src-tauri/src/omni_model_manifest.rs` — strict immutable manifest parser.
- `desktop/src-tauri/src/realtime_coordinator.rs` — Presence Session, Segment, and Epoch identity/state reducer.
- `desktop/src-tauri/src/desktop_preferences.rs` — Schema 9 storage and migration.
- `desktop/src/settings/client.ts` and Settings/Presence/Realtime consumers — Schema 9 TypeScript projection.

Each new Rust module owns pure domain logic. `lib.rs` only registers modules and
adapts commands; it does not absorb policy implementation.

---

### Task 1: Superseding Realtime Companion ADR

**Files:**
- Create: `docs/adr/0022-governed-realtime-companion.md`
- Modify: `docs/adr/0018-ephemeral-realtime-game-companion.md`
- Test: `docs/adr/0022-governed-realtime-companion.md`

**Interfaces:**
- Consumes: Phase 0 design at `docs/superpowers/specs/2026-07-27-realtime-companion-beta-phase-0-design.md`.
- Produces: normative ownership, persistence, backend-switching, and migration rules for all later tasks.

- [ ] **Step 1: Write the replacement ADR**

Create ADR 0022 with `Status: Accepted`, explicit supersession of ADR 0018's
game-only and single-provider-session assumptions, and explicit retention of the
device-local stable-caption amendment. Include these decisions verbatim as
normative statements:

```markdown
- Tauri RealtimeSessionCoordinator is the sole owner of a Presence Session.
- A Backend Segment contains exactly one Local or Cloud backend.
- Backend changes require an explicit user action and create a new Segment.
- Context Epoch rotation never carries raw media or provider-hidden state.
- Core issues the Persona Snapshot used by every backend.
- The Worker and Omni runtime submit candidates; all external actions use Core.
- Stable public captions remain the sole automatic content persistence exception.
```

Amend ADR 0018 with a header notice pointing readers to ADR 0022 while preserving
its historical status and caption amendment.

- [ ] **Step 2: Validate the ADR contains every locked boundary**

Run:

```powershell
rg -n "sole owner|exactly one|explicit user|Context Epoch|Persona Snapshot|Core|stable public captions" docs/adr/0022-governed-realtime-companion.md
```

Expected: each phrase maps to a concrete decision and all artifact metadata rules
are explicit.

- [ ] **Step 3: Commit**

```powershell
git add docs/adr/0018-ephemeral-realtime-game-companion.md docs/adr/0022-governed-realtime-companion.md
git commit -m "docs(adr): govern realtime companion backends"
```

---

### Task 2: Backend-neutral Rust domain contracts

**Files:**
- Create: `desktop/src-tauri/crates/realtime-worker/src/backend.rs`
- Modify: `desktop/src-tauri/crates/realtime-worker/src/lib.rs`
- Modify: `desktop/src-tauri/crates/realtime-worker/src/session.rs`
- Test: `desktop/src-tauri/crates/realtime-worker/src/backend.rs`
- Test: `desktop/src-tauri/crates/realtime-worker/src/session.rs`

**Interfaces:**
- Consumes: existing `ProviderKind`, `SecretString`, capture scope, and bounded control-frame implementation.
- Produces:
  - `RealtimeBackendKind`
  - `RealtimeCloudProviderKind`
  - `RealtimeActivityProfile`
  - `RealtimeInteractionIntensity`
  - `RealtimeVoiceOutput`
  - `BackendStartRequest`
  - `validate_backend_start(&BackendStartRequest) -> Result<(), StartValidationError>`

- [ ] **Step 1: Write failing backend-validation tests**

Add tests that build these requests:

```rust
fn local_request() -> BackendStartRequest {
    BackendStartRequest {
        session_id: "session-1".into(),
        segment_id: "segment-1".into(),
        context_epoch: 1,
        backend: RealtimeBackendKind::LocalMiniCpmO45,
        cloud_provider: None,
        cloud_credential_present: false,
        persona_snapshot_present: true,
        activity_profile: RealtimeActivityProfile::Auto,
        interaction_intensity: RealtimeInteractionIntensity::Standard,
        voice_output: RealtimeVoiceOutput::FairyVoice,
        source_id: Some(42),
        microphone_enabled: true,
        screen_enabled: true,
        application_audio_enabled: false,
        online_assistance_enabled: false,
    }
}
```

Assert:

- the local request passes without a credential;
- local + cloud provider fails;
- local + provider-native voice fails;
- cloud without provider or credential fails;
- cloud with provider and credential passes;
- screen and application audio require a selected source;
- all identity strings and Persona Snapshot presence are required.

- [ ] **Step 2: Run the tests and verify failure**

Run:

```powershell
cargo test -p fairy-realtime-worker backend -- --nocapture
```

Expected: compilation fails because `backend` and its types do not exist.

- [ ] **Step 3: Implement the backend domain**

Create serde snake-case enums and `BackendStartRequest`. Implement validation
with backend-specific branches:

```rust
match request.backend {
    RealtimeBackendKind::LocalMiniCpmO45 => {
        valid &= request.cloud_provider.is_none();
        valid &= !request.cloud_credential_present;
        valid &= request.voice_output != RealtimeVoiceOutput::ProviderNativeVoice;
    }
    RealtimeBackendKind::CloudLive => {
        valid &= request.cloud_provider.is_some();
        valid &= request.cloud_credential_present;
    }
}
```

Keep the old active start path compiling in this task. Re-export new types from
`lib.rs`. Make `session.rs::validate_start` delegate common capture validation to
the new request validator only after preserving its current cloud signature.

- [ ] **Step 4: Run focused and crate tests**

Run:

```powershell
cargo test -p fairy-realtime-worker backend
cargo test -p fairy-realtime-worker
```

Expected: all tests pass and the live GLM test remains ignored unless explicitly
requested with credentials.

- [ ] **Step 5: Commit**

```powershell
git add desktop/src-tauri/crates/realtime-worker/src/backend.rs desktop/src-tauri/crates/realtime-worker/src/lib.rs desktop/src-tauri/crates/realtime-worker/src/session.rs
git commit -m "feat(realtime): define backend-neutral contracts"
```

---

### Task 3: Core Realtime Persona Snapshot

**Files:**
- Create: `core/src/fairy_core/persona/realtime.py`
- Modify: `core/src/fairy_core/persona/__init__.py`
- Modify: `core/src/fairy_core/contracts/persona.py`
- Modify: `core/src/fairy_core/contracts/methods.py`
- Modify: `core/src/fairy_core/application/realtime_service.py`
- Modify: `core/src/fairy_core/application/service.py`
- Test: `core/tests/test_realtime_persona_snapshot.py`
- Test: `core/tests/realtime/test_realtime_service.py`
- Test: `core/tests/test_core_service.py`

**Interfaces:**
- Consumes: `PersonaAuthority`, its canonical `digest`, locale, profile, intensity, and bounded short-memory input.
- Produces:
  - `RealtimePersonaSnapshotInput`
  - `RealtimePersonaSnapshotModel`
  - `project_realtime_persona_snapshot(...)`
  - RPC method `realtime.persona.snapshot`

- [ ] **Step 1: Write failing projector tests**

Test this call:

```python
snapshot = project_realtime_persona_snapshot(
    authority=load_default_persona_authority(),
    locale="zh-CN",
    activity_profile="game",
    interaction_intensity="standard",
    current_goal="Finish Phase 0",
    subject_title=None,
    recent_progress=None,
)
```

Assert:

- `identity.name == "Fairy"`;
- `persona_digest` equals the canonical authority digest;
- repeated calls serialize to identical canonical JSON;
- unsupported locale/profile/intensity fails validation;
- short-memory strings are bounded and optional;
- no full Persona prompt or hidden reasoning field is returned.

- [ ] **Step 2: Run the tests and verify failure**

Run:

```powershell
python -m pytest core/tests/test_realtime_persona_snapshot.py -q
```

Expected: import failure for the new projector.

- [ ] **Step 3: Implement the projector and Pydantic contracts**

Use frozen dataclasses internally and Pydantic RPC models externally. The
projector must copy the existing authority digest rather than inventing a new
identity digest. Add `realtime.persona.snapshot` to `CORE_METHODS` and inject the
already-loaded `PersonaAuthority` into `RealtimeService`.

The handler signature is:

```python
def persona_snapshot(self, request: BaseModel) -> RealtimePersonaSnapshot:
    validated = cast(RealtimePersonaSnapshotInput, request)
    return project_realtime_persona_snapshot(
        authority=self._persona_authority,
        locale=validated.locale,
        activity_profile=validated.activity_profile,
        interaction_intensity=validated.interaction_intensity,
        current_goal=validated.current_goal,
        subject_title=validated.subject_title,
        recent_progress=validated.recent_progress,
    )
```

- [ ] **Step 4: Run focused Core tests and regenerate contracts**

Run:

```powershell
python -m pytest core/tests/test_realtime_persona_snapshot.py core/tests/realtime/test_realtime_service.py core/tests/test_core_service.py -q
powershell -ExecutionPolicy Bypass -File scripts/generate-contracts.ps1
```

Expected: tests pass; `contracts/openapi.json`,
`cloud/src/generated/api.py`, and `desktop/src/core/generated/api.d.ts` contain
the new method and models without unrelated drift.

- [ ] **Step 5: Commit**

```powershell
git add core/src/fairy_core/persona core/src/fairy_core/contracts/persona.py core/src/fairy_core/contracts/methods.py core/src/fairy_core/application/realtime_service.py core/src/fairy_core/application/service.py core/tests/test_realtime_persona_snapshot.py core/tests/realtime/test_realtime_service.py core/tests/test_core_service.py contracts/openapi.json cloud/src/generated/api.py desktop/src/core/generated/api.d.ts
git commit -m "feat(core): project realtime persona snapshots"
```

---

### Task 4: Hardware report and model-manifest policy

**Files:**
- Create: `desktop/src-tauri/src/hardware_capabilities.rs`
- Create: `desktop/src-tauri/src/omni_model_manifest.rs`
- Modify: `desktop/src-tauri/src/lib.rs`
- Test: `desktop/src-tauri/src/hardware_capabilities.rs`
- Test: `desktop/src-tauri/src/omni_model_manifest.rs`

**Interfaces:**
- Consumes: injected probe facts only; no direct DXGI/CUDA implementation in Phase 0.
- Produces:
  - `HardwareCapabilityFacts`
  - `HardwareCapabilityReport`
  - `LocalBetaReadinessReason`
  - `evaluate_local_beta_readiness(&HardwareCapabilityFacts, RealtimeActivityProfile) -> HardwareCapabilityReport`
  - `OmniModelManifest::parse_and_validate(&[u8])`

- [ ] **Step 1: Write failing policy tests**

Create table-driven tests proving:

```rust
assert_eq!(
    evaluate_local_beta_readiness(&facts_with_vram_gib(12), RealtimeActivityProfile::Focus)
        .reason,
    LocalBetaReadinessReason::VramBelow16gb
);
```

Also cover unsupported OS/architecture/vendor, missing AVX2, CUDA failure,
adapter mismatch, missing/invalid model, missing/quarantined runtime, insufficient
disk, and insufficient current budget for Focus/Auto/Game reserves.

Manifest tests reject zero sizes, uppercase or malformed SHA-256, HTTP URLs,
absolute paths, `..`, duplicate case-folded paths, empty URLs, unpinned upstream
revision, and missing patch digest.

- [ ] **Step 2: Run the tests and verify failure**

Run:

```powershell
cargo test -p fairy-desktop-v3 hardware_capabilities -- --nocapture
cargo test -p fairy-desktop-v3 omni_model_manifest -- --nocapture
```

Expected: module imports fail.

- [ ] **Step 3: Implement pure fail-closed evaluators**

Use byte constants:

```rust
const GIB: u64 = 1024 * 1024 * 1024;
const MIN_DEDICATED_VRAM: u64 = 16 * GIB;
const FOCUS_RESERVE: u64 = 3 * GIB;
const AUTO_RESERVE: u64 = 4 * GIB;
const GAME_RESERVE: u64 = 5 * GIB;
```

Require `available_budget >= predicted_peak + renderer_reserve + profile_reserve`
with checked arithmetic. The report must distinguish static eligibility from
current availability and default to unavailable if any fact is unknown.

The manifest parser uses `serde_json`, `Path::components`, URL prefix validation,
positive size checks, exact 64-character lowercase hex digests, and a
case-insensitive path set.

Register modules in `lib.rs` but expose no Tauri command and start no probe.

- [ ] **Step 4: Run focused Rust tests**

Run:

```powershell
cargo test -p fairy-desktop-v3 hardware_capabilities
cargo test -p fairy-desktop-v3 omni_model_manifest
cargo clippy -p fairy-desktop-v3 --all-targets -- -D warnings
```

Expected: all pass without loading CUDA, touching the network, or inspecting the
host GPU.

- [ ] **Step 5: Commit**

```powershell
git add desktop/src-tauri/src/hardware_capabilities.rs desktop/src-tauri/src/omni_model_manifest.rs desktop/src-tauri/src/lib.rs
git commit -m "feat(desktop): define local beta readiness contracts"
```

---

### Task 5: Tauri Presence Session coordinator state model

**Files:**
- Create: `desktop/src-tauri/src/realtime_coordinator.rs`
- Modify: `desktop/src-tauri/src/lib.rs`
- Test: `desktop/src-tauri/src/realtime_coordinator.rs`

**Interfaces:**
- Consumes: backend/profile/intensity enums and Persona digest.
- Produces:
  - `PresenceSessionIdentity`
  - `BackendSegmentIdentity`
  - `ContextEpochIdentity`
  - `RealtimeCoordinatorState`
  - `RealtimeCoordinatorEvent`
  - `RealtimeCoordinatorState::apply(event)`
  - `RealtimeCoordinatorState::accepts_result(session_id, segment_id, epoch)`

- [ ] **Step 1: Write failing reducer tests**

Cover this sequence:

```rust
let mut state = RealtimeCoordinatorState::start(start_request);
let first = state.active_identity().clone();
state.apply(RealtimeCoordinatorEvent::RotateContext {
    reason: ContextRotationReason::PrivacyResume,
});
assert!(!state.accepts_result(&first.session_id, &first.segment_id, first.epoch));
assert!(state.accepts_result(
    &state.active_identity().session_id,
    &state.active_identity().segment_id,
    state.active_identity().epoch,
));
```

Also prove:

- backend change requires `UserApprovedBackendChange`;
- backend change creates a new segment and epoch;
- profile `Auto -> Game/Focus` rotates context without inventing a Persona;
- pause clears media generation and resume creates a new epoch;
- terminal state rejects all results;
- session maximum is 240 minutes unless explicitly extended.

- [ ] **Step 2: Run the tests and verify failure**

Run:

```powershell
cargo test -p fairy-desktop-v3 realtime_coordinator -- --nocapture
```

Expected: module import failure.

- [ ] **Step 3: Implement the pure coordinator reducer**

The reducer owns identity and policy state only in Phase 0. It does not spawn the
Worker, touch devices, call Core, or emit Tauri events. Use monotonically
increasing `u64` epoch values with checked increments. Require a matching Persona
digest on segment transitions.

Register the module in `lib.rs` without constructing it at application startup.

- [ ] **Step 4: Run focused tests and clippy**

Run:

```powershell
cargo test -p fairy-desktop-v3 realtime_coordinator
cargo clippy -p fairy-desktop-v3 --all-targets -- -D warnings
```

Expected: all pass and ordinary startup behavior is unchanged.

- [ ] **Step 5: Commit**

```powershell
git add desktop/src-tauri/src/realtime_coordinator.rs desktop/src-tauri/src/lib.rs
git commit -m "feat(desktop): model realtime session ownership"
```

---

### Task 6: Desktop Preferences Schema 9

**Files:**
- Modify: `desktop/src-tauri/src/desktop_preferences.rs`
- Modify: `desktop/src/settings/client.ts`
- Modify: `desktop/src/settings/SettingsApp.tsx`
- Modify: `desktop/src/realtime/RealtimeCompanion.tsx`
- Modify: `desktop/src/realtime/RealtimeCompanionWindowApp.test.tsx`
- Modify: `desktop/src/realtime/RealtimeCompanion.test.tsx`
- Modify: `desktop/src/settings/SettingsApp.test.tsx`
- Modify: `desktop/src/app/App.test.tsx`
- Modify: `desktop/src/presence/PresenceApp.test.tsx`
- Modify: `desktop/src/presence/DualSurface.test.tsx`
- Modify: `desktop/src/presence/host/petHost.ts`
- Modify: `desktop/src/presence/host/rendererHealthHost.test.ts`
- Modify: `desktop/src/presence/transport/renderSettings.test.ts`
- Test: `desktop/src-tauri/src/desktop_preferences.rs`

**Interfaces:**
- Consumes: v8 stored preferences.
- Produces: Schema 9 enums and fields from the approved design, plus deterministic v8 migration.

- [ ] **Step 1: Write failing Rust migration tests**

Add a serialized v8 fixture and assert:

```rust
assert_eq!(migrated.schema_version, 9);
assert!(!migrated.realtime_beta_enabled);
assert_eq!(migrated.realtime_backend, RealtimeBackendPreference::Auto);
assert_eq!(
    migrated.realtime_cloud_provider,
    RealtimeCloudProviderPreference::GlmRealtimeFlash
);
assert_eq!(
    migrated.realtime_voice_output,
    RealtimeVoiceOutputPreference::ProviderNativeVoice
);
assert!(!migrated.realtime_allow_cloud_fallback);
assert!(!migrated.realtime_online_assistance_enabled);
```

Add fresh-default tests for `fairy_voice`, `auto + standard`, 240-minute Presence
maximum, 180-minute cloud daily limit, and 10-minute local keep-warm.

- [ ] **Step 2: Run focused Rust tests and verify failure**

Run:

```powershell
cargo test -p fairy-desktop-v3 desktop_preferences -- --nocapture
```

Expected: assertions fail because schema 8 fields still exist.

- [ ] **Step 3: Implement Schema 9 and migration**

Replace the public Realtime fields with typed enums:

```rust
pub realtime_beta_enabled: bool,
pub realtime_backend: RealtimeBackendPreference,
pub realtime_cloud_provider: RealtimeCloudProviderPreference,
pub realtime_allow_cloud_fallback: bool,
pub realtime_activity_profile: RealtimeActivityProfile,
pub realtime_interaction_intensity: RealtimeInteractionIntensity,
pub realtime_voice_output: RealtimeVoiceOutputPreference,
pub realtime_game_audio_default: bool,
pub realtime_online_assistance_enabled: bool,
pub realtime_memory_enabled: bool,
pub realtime_presence_max_minutes: u16,
pub realtime_cloud_daily_limit_minutes: u16,
pub realtime_local_keep_warm_minutes: u8,
```

Deserialize v8 through a dedicated legacy projection so an invalid or missing new
field never resets unrelated preferences. Validation accepts Presence maximum
`30..=240`, cloud daily values `30|60|120|180`, and keep-warm `0..=30`.

- [ ] **Step 4: Update TypeScript consumers and Settings copy**

Update `DesktopPreferences` in `settings/client.ts`, then replace test fixtures
and UI controls. The Realtime settings block must show Beta disabled by default,
Backend, Cloud Provider, Profile, Intensity, Voice Output, cloud fallback,
application audio, assistance, memory, Presence maximum, cloud daily limit, and
local keep-warm.

Do not display a Local-ready claim. The local option copy is:

```text
Local Beta requires a supported NVIDIA GPU, verified model and runtime, and enough current GPU memory.
```

- [ ] **Step 5: Run focused and type tests**

Run:

```powershell
cargo test -p fairy-desktop-v3 desktop_preferences
npx vitest run src/settings/SettingsApp.test.tsx src/realtime/RealtimeCompanion.test.tsx src/realtime/RealtimeCompanionWindowApp.test.tsx src/app/App.test.tsx src/presence/PresenceApp.test.tsx src/presence/DualSurface.test.tsx
npx tsc --noEmit
```

Expected: all pass; no fixture retains `realtime_provider` or
`realtime_voice_mode`.

- [ ] **Step 6: Commit**

```powershell
git add desktop/src-tauri/src/desktop_preferences.rs desktop/src/settings desktop/src/realtime desktop/src/app/App.test.tsx desktop/src/presence
git commit -m "feat(desktop): migrate realtime preferences to schema nine"
```

---

### Task 7: Activate Worker protocol v2 without enabling Local Beta

**Files:**
- Modify: `desktop/src-tauri/crates/realtime-worker/src/protocol.rs`
- Modify: `desktop/src-tauri/crates/realtime-worker/src/main.rs`
- Modify: `desktop/src-tauri/crates/realtime-worker/src/runtime.rs`
- Modify: `desktop/src-tauri/crates/realtime-worker/src/provider.rs`
- Modify: `desktop/src-tauri/crates/realtime-worker/src/transport.rs`
- Modify: `desktop/src-tauri/crates/realtime-worker/src/lib.rs`
- Modify: `desktop/src-tauri/src/realtime_worker.rs`
- Modify: `desktop/src-tauri/src/lib.rs`
- Modify: `desktop/src/realtime/RealtimeCompanion.tsx`
- Modify: `desktop/src/settings/client.ts`
- Test: `desktop/src-tauri/crates/realtime-worker/src/protocol.rs`
- Test: `desktop/src-tauri/crates/realtime-worker/src/main.rs`
- Test: `desktop/src-tauri/src/realtime_worker.rs`
- Test: `desktop/src/realtime/RealtimeCompanion.test.tsx`

**Interfaces:**
- Consumes: Tasks 2, 3, and 6 contracts.
- Produces: active `fairy-realtime-worker-v2` cloud path with Core-issued Persona Snapshot and no hard-coded Worker Persona.

- [ ] **Step 1: Write failing v2 wire tests**

Round-trip a Cloud `HostCommand::Start` containing:

```rust
HostCommand::Start {
    session_id: "session-1".into(),
    segment_id: "segment-1".into(),
    context_epoch: 1,
    backend: RealtimeBackendKind::CloudLive,
    cloud_provider: Some(RealtimeCloudProviderKind::GeminiLive),
    cloud_credential: Some(SecretString::from("secret".to_owned())),
    persona_snapshot: SecretString::from(valid_snapshot_json()),
    activity_profile: RealtimeActivityProfile::Auto,
    interaction_intensity: RealtimeInteractionIntensity::Standard,
    voice_output: RealtimeVoiceOutput::FairyVoice,
    source_id: Some(42),
    microphone_enabled: true,
    screen_enabled: true,
    application_audio_enabled: false,
    online_assistance_enabled: false,
}
```

Assert the debug output redacts both secret fields. Add round trips for every
frozen Worker event type and a test that serialized diagnostics reject unknown
content fields through `deny_unknown_fields`.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
cargo test -p fairy-realtime-worker protocol -- --nocapture
```

Expected: compile failure because active protocol still uses v1 fields.

- [ ] **Step 3: Switch the Rust Worker and Tauri adapter atomically**

Change ready protocol to `fairy-realtime-worker-v2`. For Cloud start:

- require `backend = cloud_live`;
- resolve the existing provider transport from `cloud_provider`;
- consume the optional credential only after backend validation;
- pass the validated Persona Snapshot JSON as `system_instruction`;
- map `application_audio_enabled` to the existing capture flag without changing
  the current mixer in Phase 0; and
- return `LOCAL_BACKEND_NOT_IMPLEMENTED` if a Local start reaches the inactive
  command path.

Delete `companion_instruction()`. The only runtime instruction source is the
Core-issued snapshot received through Tauri.

Tauri fetches `realtime.persona.snapshot` before starting the Worker, obtains the
cloud credential only for `CloudLive`, and never starts a Worker when
`realtime_beta_enabled` is false.

- [ ] **Step 4: Update the React control call**

Map Schema 9 cloud preferences to the Tauri start input. The control panel remains
cloud-capable, but a Local request renders the fail-closed readiness state and
does not invoke `realtime_worker_start`.

Add tests proving:

- disabled Beta cannot start;
- Local Beta cannot start from contract-only capability state;
- Cloud start includes profile, intensity, voice, and authorization flags;
- no credential or Persona body is exposed in component errors.

- [ ] **Step 5: Run Phase 0 focused gates**

Run:

```powershell
cargo fmt --all -- --check
cargo test -p fairy-realtime-worker
cargo test -p fairy-desktop-v3 realtime_worker
python -m pytest core/tests/test_realtime_persona_snapshot.py core/tests/realtime/test_realtime_service.py core/tests/test_core_service.py -q
npx vitest run src/realtime/RealtimeCompanion.test.tsx src/realtime/RealtimeCompanionWindowApp.test.tsx src/settings/SettingsApp.test.tsx
npx tsc --noEmit
```

Expected: all pass, with no live provider call.

- [ ] **Step 6: Commit**

```powershell
git add desktop/src-tauri/crates/realtime-worker desktop/src-tauri/src/realtime_worker.rs desktop/src-tauri/src/lib.rs desktop/src/realtime desktop/src/settings/client.ts
git commit -m "refactor(realtime): activate governed worker protocol"
```

---

### Task 8: Phase 0 acceptance and lifecycle gate

**Files:**
- Create: `docs/acceptance/realtime-companion-beta-phase-0.md`
- Modify: tests only if the gate discovers a deterministic regression.

**Interfaces:**
- Consumes: all Phase 0 tasks.
- Produces: evidence that contracts are complete while Local runtime remains inactive.

- [ ] **Step 1: Run static privacy and boundary searches**

Run:

```powershell
rg -n "companion_instruction|realtime_provider|realtime_voice_mode|Fairy Game Companion" desktop/src desktop/src-tauri
rg -n "caption|prompt|credential|audio|image|provider_payload" desktop/src-tauri/crates/realtime-worker/src desktop/src-tauri/src/realtime_worker.rs
```

Expected: the first search finds no active legacy contract or hard-coded game
Persona; the second search is reviewed so secret/content values appear only in
typed transient fields, never diagnostic formatting or logs.

- [ ] **Step 2: Run complete Phase 0 gates**

Run:

```powershell
npx tsc --noEmit
npx vitest run
python -m pytest core/tests -q
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
npx playwright test
```

Expected:

- complete Desktop Vitest passes;
- complete Core pytest passes;
- Rust format, clippy, unit, and integration tests pass;
- complete Playwright passes in the controlled single-Vite lifecycle;
- the credential-gated live GLM test remains ignored;
- no Docker, release, model, CUDA, Voice, Realtime, or Omni process is started.

- [ ] **Step 3: Run one native startup smoke**

Start `npm run tauri -- dev` in the Desktop directory. Verify:

- one user-visible Fairy main window;
- `CORE READY`;
- Settings opens in the main WebView;
- Realtime Beta is disabled by default;
- Settings shows no false Local-ready claim;
- no Voice, Realtime, Omni, model download, or CUDA worker starts.

Stop the exact spawned process tree and remove generated `desktop/test-results`
and `desktop/.tmp/release-trace` only after resolving both paths inside the
Desktop root.

- [ ] **Step 4: Record acceptance evidence**

Document commands, pass counts, ignored credential-gated tests, native process
observations, and explicitly unrun later-phase gates in
`docs/acceptance/realtime-companion-beta-phase-0.md`.

- [ ] **Step 5: Commit**

```powershell
git add docs/acceptance/realtime-companion-beta-phase-0.md
git commit -m "test(realtime): certify phase zero contracts"
```

Phase 0 completion immediately starts a separate Phase 1 design/plan cycle for
real DXGI/CUDA probes and managed model installation. Passing Phase 0 never
authorizes a Local-ready UI claim.
