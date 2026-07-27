# Realtime Companion Beta Phase 8 Acceptance

Date: 2026-07-28

## Decision

Phase 8 release engineering is complete: Fairy now has an MSI-only packaging
contract, third-party notices, support/privacy/troubleshooting disclosures,
strict bundle inspection, bounded release evidence, and a fail-closed Beta
release gate.

Fairy Realtime Companion Beta is **not approved for public release** from this
machine. The production Tauri build created and probed the Core sidecar and
created the Voice runtime, then stopped before Omni, WiX, and MSI generation
because the CUDA Toolkit is unavailable. No installer candidate therefore
exists for install/upgrade/uninstall, signing, signature verification, or
malware scanning. The mandatory four-hour production-model soak, native
WebView2 recovery evidence, and reference-device performance/privacy evidence
also remain absent.

This record certifies the Phase 8 implementation and its rejection behavior. It
does not convert missing physical or release-candidate evidence into a pass.

## Release contract and disclosures

- The supported Windows artifact is MSI with external cabinet files. The
  generated media manifest must enumerate the exact MSI/cabinet set, sizes,
  and SHA-256 digests.
- MiniCPM model weights are downloaded into the managed local model store and
  are forbidden from the installer. Production Omni runtime staging no longer
  requires a model source merely to build the runtime, while an explicit model
  probe remains strict.
- The bundle contains the Realtime Companion support matrix, privacy
  boundaries, troubleshooting guidance, top-level third-party notices, and
  relevant dependency license texts.
- Voice packaging preserves the CosyVoice and Matcha-TTS root licenses in
  addition to installed Python dependency licenses.
- Bundle inspection rejects model weights, databases, logs, recordings,
  environment files, legacy Qt `main.py`, test results, duplicate cabinets,
  missing disclosures, and incomplete license sets.
- Release evidence accepts only exact, bounded schemas with relative artifact
  paths, fresh timestamps, content-free diagnostics, verified file sizes and
  hashes, and quantitative production thresholds.
- The gate refuses to overwrite an existing report and emits a bounded
  `passed` or `blocked` JSON result. A blocked gate exits nonzero.

## Automated certification evidence

The final repository gate
`scripts/test-all.ps1 -SkipDocker` completed successfully:

- Release disclosures, bundle policy, release-gate fixtures, Omni runtime
  policy fixtures, and Voice license fixtures passed.
- Sandbox tests passed 49 tests with one existing platform skip.
- Core passed 869 tests in 509.33 seconds; locked Ruff check and format passed.
- Capabilities passed 118 tests.
- Cloud unit tests passed 123 tests with one existing skip and 29
  PostgreSQL-backed integration tests deselected.
- Alembic upgrade and downgrade SQL generation completed.
- Rust format, Clippy with warnings denied, and the complete workspace test
  suite passed. The credential-gated live GLM test remained explicitly
  ignored.
- The deterministic four-hour-equivalent Realtime soak passed.
- The bundled Core executable was rebuilt, probed, and hashed as
  `459d815dc73b2bf13bf729414e257fe147dc0dbdb09b6dabdbb9ad01d239efd6`.
- Pinned MinGit `2.55.0.windows.2` was composed and probed with the Core and
  Rust worker.
- Complete Vitest passed 98 files / 530 tests. The known jsdom Canvas warning
  did not skip or fail a test.
- Complete Playwright passed 79 tests in one controlled Vite lifecycle. The
  performance project completed in about 1.2 seconds.
- TypeScript and the production Vite build passed. Core readiness was
  1,590 ms against a 3,000 ms budget; initial renderer gzip was 733.7 KiB
  against an 800 KiB budget.
- Generated contracts were unchanged, and `git diff --check` passed.

Release-gate fixtures cover a passing candidate plus blocked, missing,
malformed, unknown-field, path-traversal, digest, stale-evidence, threshold,
and output-overwrite cases. A separate final audit used the published blocked
example as input. It exited with code 1 and reported every absent mandatory
domain as blocked, including four-hour soak, native WebView2, reference
performance, privacy, installer lifecycle, security, and artifacts.

## Production packaging evidence

An actual `npm run tauri build` attempt was made outside the restricted build
cache:

- Core production sidecar build and probe passed.
- Voice production runtime build passed and staged 7,666 files at about
  8.4 GB. Its executable SHA-256 was
  `10b3c7585ccc40fed50e79fb5155782cff92fc918f612a588545576b944548f2`.
- The follow-up license audit found that the upstream CosyVoice and Matcha-TTS
  root licenses were not previously staged. Packaging and bundle validation
  were corrected, and focused regression fixtures passed.
- Omni production runtime staging correctly excluded model weights, then
  stopped with `CUDA Toolkit not found`.
- Because Omni did not build, the frontend/WiX packaging portion of that
  production command did not produce an MSI or cabinet set.

Packaging success is not inferred from the successful Core and Voice inputs.

## Mocked, deterministic, and real boundaries

The release scripts and fixtures exercise exact schemas, file inventories,
hashes, policy thresholds, and failure behavior with controlled data. Vitest
uses mocked Tauri transport; Playwright uses the governed desktop fixture.
Rust and Python suites exercise real implementation state machines with
deterministic adapters. These gates do not substitute for CUDA inference,
physical audio/capture devices, a signed installer, a reference game workload,
or a four-hour wall-clock session.

The WSL sandbox attestation was not required in this run. Docker was explicitly
disabled, so PostgreSQL/S3 recovery, capability Outbox, two-device sync, memory
retrieval, document/evidence, and runtime Preview integration were not
executed. Live paid GLM traffic was not run.

## Existing test changes

`scripts/test-all.ps1` now runs the Phase 8 release disclosures, bundle,
runtime-policy, license, and release-gate fixtures as part of the normal full
repository verification sequence. Existing assertions were not weakened.
Production build scripts were changed only to enforce model exclusion and
license retention; their failure conditions remain strict.

## Gates required before public Beta release

All of the following remain blocked and must be rerun against one exact
candidate:

- build the production Omni CUDA runtime and produce the MSI, all external
  cabinets, and their media manifest;
- install, cold-start, upgrade, uninstall, and verify user-data preservation;
- sign the complete candidate, verify every signature, and pass malware
  scanning;
- complete a four-hour wall-clock local MiniCPM session on a supported NVIDIA
  16 GiB+ reference device, including capture, audio, Voice, pause/resume,
  window switches, context rotations, recovery, quarantine, and cleanup;
- capture native WebView2 evidence for proposal authority, resource pressure,
  crash recovery, quarantine, Companion reload, and no-worker-preheat;
- pass production latency limits and measure median GPU frame-time impact at
  no more than 10% and game 1% Low impact at no more than 15%;
- verify selected-window privacy, sensitive-window pause, no raw media on disk,
  content-free logs, disclosed Cloud/local scope, and telemetry off by default;
- rerun the complete non-Docker and required Docker/PostgreSQL/S3/WSL gates as
  demanded by the release environment, then populate fresh evidence with the
  exact artifact hashes.

Until every item passes, the release gate must remain blocked.
