# Realtime Local Beta 12 GB Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Fairy Local Realtime Beta correctly support nominal 12 GB NVIDIA GPUs while retaining live free-VRAM and runtime self-test protection.

**Architecture:** Capability evaluation uses a decimal 12 GB static hardware floor and one 512 MiB runtime headroom above the verified model peak. Windows' live budget-minus-usage measurement remains the source of truth for transient availability, so activity profiles do not add duplicate synthetic reserves.

**Tech Stack:** Rust/Tauri, TypeScript/React, Vitest, Python release-document checks, Markdown support and acceptance documentation.

## Global Constraints

- Work only in `D:\桌面\~\deskllmchat\fairy-v3`.
- Do not touch the parent checkout's untracked `CLAUDE.md`.
- The static minimum is exactly `12_000_000_000` dedicated VRAM bytes and is shown as “12 GB”.
- Required live budget is the verified or manifest model peak plus exactly 512 MiB.
- Keep CUDA, driver, adapter-LUID, AVX2, model verification, runtime self-test, disk, and quarantine gates fail-closed.
- Preserve `LOCAL_VRAM_INSUFFICIENT` for the static backend failure.
- Do not start Voice, Realtime, Game Companion, Tauri dev, Docker, or a release build for this change.
- Historical Phase 0–8 plans and captured evidence remain historical; active release policy and the latest completion note must identify the superseding 12 GB decision.

---

### Task 1: Correct the Rust capability policy

**Files:**
- Modify: `desktop/src-tauri/src/hardware_capabilities.rs`
- Modify: `desktop/src-tauri/src/hardware_probe.rs`
- Modify: `desktop/src-tauri/src/local_readiness.rs`
- Modify: `desktop/src-tauri/src/realtime_backend_resolver.rs`

**Interfaces:**
- Consumes: `HardwareProbeReport::{dedicated_vram_bytes,budget_bytes,current_usage_bytes}` and `OmniModelManifest::predicted_peak_vram_mb`.
- Produces: `LocalBetaReadinessReason::VramBelow12gb`, `HardwareCapabilityFacts::runtime_headroom_bytes`, and `HardwareCapabilityReport::required_budget_bytes`.

- [x] **Step 1: Replace the old policy tests with failing 12 GB and unified-budget cases**

Add focused cases in `hardware_capabilities.rs` equivalent to:

```rust
#[test]
fn nominal_twelve_gb_is_in_the_supported_hardware_class() {
    let mut facts = eligible_facts();
    facts.dedicated_vram_bytes = Some(12_000_000_000);
    facts.budget_bytes = Some(12_000_000_000);
    facts.current_usage_bytes = Some(1_000_000_000);
    facts.predicted_model_peak_bytes = Some(9_500 * MIB);
    let report = evaluate_local_beta_readiness(&facts, RealtimeActivityProfile::Focus);
    assert!(report.static_eligible);
    assert!(report.local_beta_eligible);
    assert_eq!(report.required_budget_bytes, Some(10_012 * MIB));
}

#[test]
fn value_below_twelve_decimal_gb_fails_the_static_gate() {
    let mut facts = eligible_facts();
    facts.dedicated_vram_bytes = Some(11_999_999_999);
    let report = evaluate_local_beta_readiness(&facts, RealtimeActivityProfile::Focus);
    assert_eq!(report.reason, LocalBetaReadinessReason::VramBelow12gb);
}

#[test]
fn activity_profiles_do_not_duplicate_live_vram_reserves() {
    let facts = eligible_facts();
    let required = [
        RealtimeActivityProfile::Focus,
        RealtimeActivityProfile::Auto,
        RealtimeActivityProfile::Game,
    ]
    .map(|profile| evaluate_local_beta_readiness(&facts, profile).required_budget_bytes);
    assert_eq!(required, [required[0]; 3]);
}
```

Also add a 15.7 GiB regression case and a live-budget-shortfall case that expects
`InsufficientFreeVram` while `static_eligible` remains true.

- [x] **Step 2: Run the focused Rust test and confirm failure**

Run:

```powershell
cargo test hardware_capabilities --lib
```

from `desktop/src-tauri`.

Expected: compile or assertion failure because `VramBelow12gb`,
`runtime_headroom_bytes`, and the unified 10,012 MiB calculation do not exist.

- [x] **Step 3: Implement the corrected static and dynamic policy**

In `hardware_capabilities.rs`:

```rust
const MIB: u64 = 1024 * 1024;
const MIN_DEDICATED_VRAM_BYTES: u64 = 12_000_000_000;
const RUNTIME_HEADROOM: u64 = 512 * MIB;
```

Rename `VramBelow16gb` to `VramBelow12gb`, rename the internal fact
`renderer_reserve_bytes` to `runtime_headroom_bytes`, and calculate:

```rust
let required_budget = facts
    .predicted_model_peak_bytes
    .and_then(|peak| peak.checked_add(facts.runtime_headroom_bytes));
```

Do not add a profile reserve. Compare both the primary reason gate and
`static_eligible` against `MIN_DEDICATED_VRAM_BYTES`.

In `local_readiness.rs`, define `DEFAULT_RUNTIME_HEADROOM` as 512 MiB and project
it to `facts.runtime_headroom_bytes`. Update empty fixtures in
`hardware_probe.rs` and `local_readiness.rs`. Update the backend resolver match
arm to map `VramBelow12gb` to the unchanged `LOCAL_VRAM_INSUFFICIENT` code.

- [x] **Step 4: Run the focused Rust tests**

Run:

```powershell
cargo test hardware_capabilities --lib
cargo test local_readiness --lib
cargo test realtime_backend_resolver --lib
```

Expected: all selected tests pass, including exact 12 GB, 15.7 GiB, unified
headroom, transient free-budget failure, and stable backend-code coverage.

---

### Task 2: Align Desktop readiness projection and fixtures

**Files:**
- Modify: `desktop/src/settings/client.ts`
- Modify: `desktop/src/settings/RealtimeReadinessCard.tsx`
- Modify: `desktop/src/settings/RealtimeReadinessCard.test.tsx`
- Modify: `desktop/e2e/support/coreFixture.ts`

**Interfaces:**
- Consumes: serialized Rust reason `"vram_below12gb"`.
- Produces: TypeScript `LocalBetaReadinessReason` coverage and user-facing static/transient remediation copy.

- [x] **Step 1: Change the component test fixture first**

Replace `"vram_below16gb"` with `"vram_below12gb"` and assert:

```ts
expect(
  await screen.findByText(/at least 12 GB dedicated VRAM/i),
).toBeInTheDocument();
```

Keep the separate `insufficient_free_vram` test and assert that it recommends
closing GPU-heavy applications and retrying, without claiming the adapter is
unsupported.

- [x] **Step 2: Run the component test and confirm failure**

Run:

```powershell
npx vitest run src/settings/RealtimeReadinessCard.test.tsx
```

from `desktop`.

Expected: type or text failure because the new reason and copy are not handled.

- [x] **Step 3: Update the TypeScript contract, UI copy, and E2E fixture**

Change the reason union and E2E readiness fixture to `"vram_below12gb"`. Update
`readinessReason()` to return:

```ts
if (reason === "vram_below12gb") {
  return "Local Beta requires an NVIDIA GPU with at least 12 GB dedicated VRAM.";
}
```

Update the transient text to describe current GPU use rather than a
profile-specific synthetic reserve.

- [x] **Step 4: Run Desktop checks**

Run from `desktop`:

```powershell
npx vitest run src/settings/RealtimeReadinessCard.test.tsx
npx tsc --noEmit
```

Expected: both commands pass with no old reason left in active Desktop code or
fixtures.

---

### Task 3: Correct active support policy and acceptance status

**Files:**
- Modify: `docs/release/realtime-companion-beta-support.md`
- Modify: `docs/release/realtime-companion-beta-troubleshooting.md`
- Modify: `docs/adr/0022-governed-realtime-companion.md`
- Modify: `docs/acceptance/realtime-companion-beta-completion.md`
- Modify: `scripts/check-release-documents.py`

**Interfaces:**
- Consumes: the implemented 12 GB static reason and unified live-budget behavior.
- Produces: release-document assertions for “at least 12 GB dedicated VRAM” and `` `vram_below12gb` ``.

- [x] **Step 1: Make the release-document test require the new policy**

Change `scripts/check-release-documents.py` to require:

```python
"at least 12 GB dedicated VRAM",
"`vram_below12gb`",
```

Run:

```powershell
python scripts/check-release-documents.py
```

Expected: failure until the support and troubleshooting documents are updated.

- [x] **Step 2: Update active policy documents**

Update the support matrix to offer Local Beta for qualifying NVIDIA 12 GB+
hardware. Remove the row that groups 12 GiB with unsupported 8 GiB hardware.
Explain that 12 GB identifies the supported hardware class but live workload,
model verification, and self-test still determine current availability.

Update troubleshooting to use `vram_below12gb`; describe
`insufficient_free_vram` as current GPU budget pressure and remove the obsolete
instruction to choose Focus merely to reduce a synthetic reserve.

Amend ADR 0022's active hard-gate paragraph to point to the superseding
2026-07-30 decision: decimal 12 GB static floor plus verified model peak and
512 MiB live headroom.

- [x] **Step 3: Preserve truthful historical acceptance evidence**

Add a dated addendum to
`docs/acceptance/realtime-companion-beta-completion.md` stating that the earlier
15.7 GiB rejection was evidence for the old 16 GiB policy, is superseded by the
12 GB design, and requires a new native readiness check before public release.
Do not rewrite the captured Phase 0–8 results as if they had passed under the
new code.

- [x] **Step 4: Run document and stale-policy checks**

Run:

```powershell
python scripts/check-release-documents.py
rg -n "VramBelow16gb|vram_below16gb|at least 16 GiB physical VRAM" desktop scripts docs/release docs/adr/0022-governed-realtime-companion.md
```

Expected: the release checker passes and the active-code/policy search returns
no matches. Historical plan/spec/acceptance files may still describe the
superseded policy as historical evidence.

---

### Task 4: Verify and commit the implementation

**Files:**
- Verify all files modified in Tasks 1–3.

**Interfaces:**
- Consumes: the complete implementation.
- Produces: one reversible Conventional Commit with target test evidence.

- [x] **Step 1: Run formatting and diff checks**

Run:

```powershell
cargo fmt --all -- --check
git diff --check
```

Expected: both pass.

- [x] **Step 2: Run the bounded regression gate**

Run:

```powershell
npx tsc --noEmit
npx vitest run src/settings/RealtimeReadinessCard.test.tsx
```

from `desktop`, then:

```powershell
cargo test hardware_capabilities --lib
cargo test local_readiness --lib
cargo test realtime_backend_resolver --lib
cargo clippy --all-targets -- -D warnings
```

from `desktop/src-tauri`, and finally:

```powershell
python scripts/check-release-documents.py
```

from the project root.

Expected: every bounded check passes. Native WebView2/GPU evidence remains
explicitly pending for the user-assisted gate.

- [x] **Step 3: Review the final diff and process state**

Confirm:

```powershell
git status --short
git diff --stat
```

Only the intended implementation files are modified; `../CLAUDE.md` remains
untracked. Confirm no new Node, Python, Cargo, Tauri, Core, Voice, or Realtime
process remains after the bounded commands.

- [x] **Step 4: Commit**

```powershell
git add -- desktop/src-tauri/src/hardware_capabilities.rs desktop/src-tauri/src/hardware_probe.rs desktop/src-tauri/src/local_readiness.rs desktop/src-tauri/src/realtime_backend_resolver.rs desktop/src/settings/client.ts desktop/src/settings/RealtimeReadinessCard.tsx desktop/src/settings/RealtimeReadinessCard.test.tsx desktop/e2e/support/coreFixture.ts docs/release/realtime-companion-beta-support.md docs/release/realtime-companion-beta-troubleshooting.md docs/adr/0022-governed-realtime-companion.md docs/acceptance/realtime-companion-beta-completion.md scripts/check-release-documents.py
git commit -m "fix(realtime): support 12gb local hardware"
```

Expected: a single implementation commit containing code, regression tests, and
the active policy documentation it changes.
