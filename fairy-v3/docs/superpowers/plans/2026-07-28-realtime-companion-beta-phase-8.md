# Realtime Companion Beta Phase 8 Implementation Plan

> **Design:** `docs/superpowers/specs/2026-07-28-realtime-companion-beta-phase-8-design.md`
>
> **Authority:** the user-approved Realtime Companion Beta final design and
> frozen Phase 0–7 contracts.

## Goal

Produce a truthful Windows x64 MSI release candidate, ship the required legal,
support, privacy, and troubleshooting resources, and add a machine-readable
release gate that blocks distribution when mandatory real-environment evidence
is absent or failed.

## Non-goals

This phase does not bundle model weights, enable Beta by default, add CPU
fallback, silently switch Local/Cloud, run paid providers, invent signing
credentials, weaken the four-hour/reference-GPU gates, or claim a public
release from an unsigned or environment-blocked candidate.

## Task 1: Publish the Beta release documents

**Files**

- Add `THIRD_PARTY_NOTICES.md`
- Add `docs/release/realtime-companion-beta-support.md`
- Add `docs/release/realtime-companion-beta-privacy.md`
- Add `docs/release/realtime-companion-beta-troubleshooting.md`
- Add the existing upstream license texts to `desktop/src-tauri/resources/legal`
- Update `README.md`

**Implementation**

1. Document the Local/Cloud support matrix and exact Local 16 GiB+ readiness
   qualification.
2. Document selected-window capture, Cloud upload scope, stable-caption
   persistence, proposal-governed memory, telemetry-off default, and forbidden
   diagnostic content.
3. Map stable readiness, provider, Sidecar, resource, and privacy codes to
   bounded user actions.
4. Record material shipped runtime/dependency licenses and distinguish the
   separately downloaded MiniCPM-o model.
5. Correct README to the actual MSI-only bundle and external-cabinet behavior.
6. Avoid claims of current public release, signing, paid-provider quality, or
   reference-GPU certification.

**Tests**

- required headings and exact support/policy statements;
- no NSIS claim;
- no credentials, local absolute paths, or transcript examples;
- required notice entries and license resources exist.

**Commit**

`docs(release): publish realtime beta disclosures`

## Task 2: Package legal and release resources

**Files**

- Modify `desktop/src-tauri/tauri.conf.json`
- Modify `scripts/collect-wix-media.ps1`
- Add or modify bundle-composition validation
- Add focused Rust or script tests where practical

**Implementation**

1. Map notices, support, privacy, troubleshooting, and upstream license texts
   into a stable `legal`/`docs` bundle resource layout.
2. Keep the MiniCPM model outside the installer while retaining only its
   signed/digest-pinned manifest.
3. Extend the artifact manifest to include the MSI, every external cabinet,
   and their SHA-256 digests.
4. Validate that prohibited model weights, partial downloads, databases,
   logs, credentials, test artifacts, Qt entry points, and raw media are
   absent from the bundle.
5. Keep the custom WiX per-machine upgrade and external-cabinet layout.

**Tests**

- Tauri resource map contains every release document;
- composition validator passes a controlled good tree and rejects each
  forbidden category;
- artifact manifest is deterministic, bounded, and complete;
- PowerShell parses under strict mode.

**Commit**

`build(desktop): package realtime beta disclosures`

## Task 3: Add the fail-closed release gate

**Files**

- Add `scripts/test-realtime-companion-beta-release.ps1`
- Add `scripts/test-realtime-companion-beta-release-fixtures.ps1`
- Add a release evidence schema/example under `docs/release`
- Update `README.md`

**Implementation**

1. Require a dedicated evidence directory, an evidence manifest with a fixed
   schema, and a non-existing output report path.
2. Validate Phase acceptance, complete regression, real four-hour soak,
   native WebView2 recovery/quarantine, reference CUDA latency/queue, reference
   Game Profile impact, privacy, installer lifecycle, malware scan, and
   signing.
3. Validate quantitative thresholds from the frozen final design.
4. Inspect the optional bundle directory and compare every expected artifact
   digest.
5. Emit only relative paths, hashes, timestamps, gate states, and bounded
   public reasons.
6. Return non-zero for missing, blocked, stale, malformed, or failed evidence.
7. Never create passing evidence, download a model, call a provider, or sign
   an artifact.

**Tests**

- complete passing fixture;
- every mandatory gate missing/blocked/failed;
- four-hour, rotation, pause, switch, latency, barge-in, frame-time, and 1%
  Low boundary values;
- path escape, absolute path, overwrite, malformed JSON, unexpected field,
  stale evidence, and digest mismatch;
- report contains no forbidden content fields.

**Commit**

`test(release): add realtime beta release gate`

## Task 4: Build and inspect the release candidate

**Files**

- Production sidecar and MSI outputs only; no source change unless a verified
  packaging defect is found
- Add `docs/acceptance/realtime-companion-beta-phase-8.md`

**Verification**

1. Run release-document, resource, fixture, boundary, and secret preflight.
2. Run Core Ruff and complete pytest.
3. Run Rust fmt, strict Clippy, and workspace tests.
4. Run TypeScript, complete Vitest, and complete Playwright.
5. Build the production Core, Voice, Omni, MinGit, frontend, and MSI candidate.
6. Validate MSI/external cabinet composition and SHA-256 manifest.
7. Run disposable install/start/upgrade/uninstall when the environment permits.
8. Run the release gate against real evidence. Preserve `blocked` for missing
   four-hour, native, reference-GPU, signing, or malware evidence.
9. Stop every controlled process and remove transient build/test artifacts
   that are not release outputs.

**Defect policy**

Any independent Critical or Important defect discovered during certification
is fixed with its own regression and Conventional Commit before the full gate
is rerun.

**Commit**

`test(release): certify realtime beta candidate`

## Completion

Phase 8 engineering is complete when Tasks 1–4 are committed, all
environment-independent gates pass, the candidate artifact state is recorded
truthfully, no controlled process remains, and the worktree contains no Phase
8 changes. Public Beta release remains blocked until the release gate has
passing real-environment, signing, and malware evidence.
