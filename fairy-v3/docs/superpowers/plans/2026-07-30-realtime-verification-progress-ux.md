# Realtime Verification Progress UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace misleading `Verifying 100%` feedback with truthful, phase-specific indeterminate progress while preserving real download percentages.

**Architecture:** `RealtimeReadinessCard` derives a small presentation object from the active model phase. Downloading uses the existing byte ratio; non-download work omits numeric progress and uses a CSS-only indeterminate segment with a Reduced Motion fallback.

**Tech Stack:** React, TypeScript, CSS, Vitest, Testing Library.

## Global Constraints

- Work only in `D:\桌面\~\deskllmchat\fairy-v3`.
- Do not touch `../CLAUDE.md`.
- Do not change Rust, model files, runtime state, RPC contracts, or persisted schemas.
- Do not restart the currently running Tauri development session.
- Do not start Voice, Realtime, Omni, Docker, or release-build processes.

---

### Task 1: Define the progress contract with tests

**Files:**
- Modify: `desktop/src/settings/RealtimeReadinessCard.test.tsx`

**Interfaces:**
- Consumes: existing `OmniModelInstallPhase`.
- Produces: assertions for determinate download and indeterminate verification/self-test.

- [x] Add a verification fixture with full received bytes and assert that the
  progressbar has `data-mode="indeterminate"`, has no `aria-valuenow`, does not
  show `100%`, and displays
  `Checking model integrity. Large files can take several minutes.`
- [x] Add a runtime-self-test fixture and assert indeterminate semantics plus
  `Testing CUDA and model startup without starting a Realtime session.`
- [x] Run
  `npx vitest run src/settings/RealtimeReadinessCard.test.tsx` from `desktop`
  and confirm the new assertions fail against the numeric implementation.

### Task 2: Implement phase-specific presentation

**Files:**
- Modify: `desktop/src/settings/RealtimeReadinessCard.tsx`
- Modify: `desktop/src/settings/settings-app.css`

**Interfaces:**
- Produces: internal `operationProgressPresentation(model)` returning
  `{ detail, indicator, mode }`, where mode is `"determinate"` or
  `"indeterminate"`.

- [x] Add the presentation helper with exhaustive phase copy for checking
  space, downloading, cancelling, verifying, layout check, and runtime
  self-test.
- [x] Render numeric text and ARIA values only when mode is determinate.
- [x] Add the restrained indeterminate segment animation and a static Reduced
  Motion fallback.
- [x] Re-run the focused Vitest suite and confirm all tests pass.

### Task 3: Verify and commit

**Files:**
- Verify all files changed in Tasks 1–2 and this tracked plan.

- [x] Run `npx tsc --noEmit` from `desktop`.
- [x] Run `npx vitest run src/settings/RealtimeReadinessCard.test.tsx` from
  `desktop`.
- [x] Run `git diff --check` and review the final CSS/DOM diff.
- [x] Confirm the original Tauri/Vite session remains alive and no Voice,
  Realtime, or Omni Worker was started.
- [x] Commit with
  `fix(desktop): clarify local model verification progress`.
