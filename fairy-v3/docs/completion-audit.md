# Fairy V3 Completion Audit

- Baseline audit date: 2026-07-14
- Liquid Glass amendments: 2026-07-17, 2026-07-19, and corrective boundary on 2026-07-22
- Audit repair amendment: 2026-07-21
- Product root: `fairy-v3/`
- Source baseline: the approved Fairy V3 architecture plan and the supplied
  Mojoclaw/Mojocore architecture reference
- Status vocabulary: `Proven`, `Environment-blocked`, `Missing`, and
  `Contradicted`

`Proven` means the implementation exists and the cited executable evidence ran
successfully. `Environment-blocked` means the implementation and tests exist,
but a required external runtime was absent during this audit. A skipped or
deselected test is never counted as proof. No accepted row is `Missing` or
`Contradicted`.

## 2026-07-21 Audit Repair Amendment

This amendment supersedes current-status claims below where they imply that the
July 14 environment evidence was rerun for the present source tree. The repair
establishes device-local Obsidian path authority, immutable Knowledge revisions
and Task snapshots, Harness manifests, Core-owned Memory settings and retention,
cancellable Realtime startup, full-duplex voice barge-in, multi-Source Knowledge
UI isolation, an explicit RPC transport manifest, and smaller public module
boundaries.

The current automated source gates are proven: Core (777), Capabilities (118),
Cloud unit/contract (121 with one PostgreSQL-DSN test skipped and integration
tests deselected), Desktop Vitest (357), Desktop Playwright (58), Rust workspace,
Ruff, TypeScript, Clippy, Alembic offline upgrade/downgrade, generated contract
drift, and the Vite production build passed. The Playwright performance gate ran
separately with one worker and passed.

The following are **not** current proof: Docker/PostgreSQL integration (Docker
daemon unavailable), a live Obsidian test Vault, live GLM Realtime, and an
interactive Tauri multi-screen/Presence regression. Historical evidence remains
recorded below, but these gates must be rerun before a new release is certified.
No Python static type checker is configured in this repository; Ruff is lint and
format evidence, not Python type-checking evidence.

## 2026-07-22 HostBackdrop Boundary Amendment

ADR 0020 supersedes the WGC and Desktop Duplication optical-source claims below.
The production renderer now reports `host_backdrop_identity`, does not expose
HostBackdrop pixels to the D3D shader, and does not claim continuous radial
displacement. The July 17 and July 19 measurements remain historical experiment
evidence only; they do not certify the current renderer or Apple-style
edge-responsive lensing.

Current native validation must prove the identity HostBackdrop material,
foreground identity-layer isolation, input hit regions, renderer lifecycle and
performance. True desktop refraction is an unmet capability gate until Windows
offers a supported Composition displacement path or the product explicitly
accepts a separate capture-mode tradeoff.

## Historical 2026-07-17 Liquid Glass Amendment

This amendment supersedes the Pet-specific statements below where they conflict.
`standard` optics remain procedural and perform no desktop capture. Explicit
`enhanced` optics use WGC -> D3D11 -> DirectComposition. WGC acquires the active
monitor frame, while the production shader hard-clamps sampling to the Pet
surface plus a 24-logical-pixel optical guard band. Textures remain GPU-local and
are never persisted, analyzed, uploaded, copied to the CPU, or sent through
WebView IPC. `pet-input` never captures.

The current WebGL/Canvas, TypeScript, Rust, production HLSL compilation, browser
visual, and real enhanced WGC gates are proven. The interactive Windows smoke
completed `CreateForMonitor`, zero-copy D3D11 sampling, DirectComposition
presentation, screenshot capture, and click-through checks. It measured 59.90
FPS at the 60 FPS target and 143.48 FPS at the 144 FPS target; both p95 render
latencies remained below 8 ms. Runtime cadence produced 15.13/29.95/59.61/144.23
FPS, and the native window-group gate proved WebView/DirectComposition frame
identity plus input core alignment. The earlier sandbox-only HRESULT
`0x80070424` is not a production failure. No release or Docker build was
performed for this development amendment.

The 2026-07-19 amendment also proves that the enhanced source did not regress to
single-window capture. The runtime item and selected monitor were both
2560 x 1440, source stage was `monitor_capture_started`, and
`capture_window_handle` remained null. A stationary-lens red/green composite
gate measured mean lens-pixel changes of 73.39/73.68/73.74 at 60/144/300 FPS,
showing that moving screen content updates without dragging Fairy. The same
runs measured 59.81/142.91/297.68 average FPS; the 300 FPS path reported
200.12 P1 FPS and 0.218 ms DirectComposition present p95. Bounded overlay
reconstruction and lower-alpha edge recovery removed recursive white rings
without CPU readback or pixel IPC. No release or Docker build was performed.

## Acceptance Summary

Fairy V3 implements the approved V3 architecture and the 2026-07-21 audit repair
at source and automated-contract level. This is not a fresh production-release
certification: the current environment-backed gates listed in the amendment
remain outstanding. The July 14 baseline proved Docker Compose and real WSL2
FairySandbox against that historical source state. The native CosyVoice/TensorRT worker
also passes its real RTX 5060 Ti performance gate: first-frame p95 is 423.6ms,
prefetched gap is 0ms, and cancellation response is 15.2ms. The companion is
now a dual-window Liquid Glass surface with a WebGL2/GLSL production renderer,
Canvas compatibility fallback, deterministic interaction phases, and a
Rust-owned placement and cursor coordinator. Its default privacy-safe optical
route uses screen-locked procedural illumination, SDF thickness and normals,
bounded RGB dispersion, and directional caustics. An explicit enhanced route
performs bounded-sampling GPU-only live refraction without giving the WebView or
Companion a general capture API.

## Implementation Changes

| Requirement | Status | Implementation | Executable evidence |
| --- | --- | --- | --- |
| 1. Core Foundation | Proven | `core/src/fairy_core/domain`, `application`, `commanding`, `storage`, `runtime`, `memory`, `workspace`, and typed contracts define Project, Conversation, Task, Version, Workspace, Changeset, Approval, CommandRun/Event, Runtime, Preview, Artifact, Checkpoint, and Hermes Memory. | Core full suite: 607 passed; state, execution, storage, memory, workspace, and contract suites. |
| 2. Command, permission, and recovery authority | Proven | One thread-safe `ToolRegistry` generates Agent tools, capability manifest, policy metadata, and Slash availability. Device/cloud profiles and toggles are Core-owned. All model-visible schemas are closed. | `test_policy.py`, `test_execution_settings.py`, `test_command_bus.py`, `test_ledger.py`, `test_persistence_recovery.py`, and assistant approval/recovery tests. |
| 3. Local project closure | Proven | Imported sources are copied to managed Git; each Task gets an isolated worktree. Changesets, dependency templates, Review, Preview, Checkpoint, accept, and discard are Core-orchestrated. WSL execution requires attestation and has no host fallback. | Local/Rust closure suites plus real FairySandbox 1.0.0 attestation and structured WSL execution passed. |
| 4. Cloud and multi-device | Proven | PostgreSQL schema/leases/outbox, S3 object adapter, REST/SSE, optimistic Project revision, candidate conflict retention, execution worker, Runtime worker, and private Preview gateway are implemented. No Redis or NATS is present. | Cloud unit/contract suite: 112 passed; Alembic full upgrade/downgrade; 27 live PostgreSQL 18.4/S3/RLS/OCI/two-device integration tests passed in Docker. |
| 5. Complete non-Legacy capabilities | Proven | Scratch/project assistant, local/cloud provider profiles, governed web/research/information, documents/RAG, Hermes, perception, voice, Presence/Pet, Slash Commands, typed system actions, Skills, and MCP all use Core contracts. | Capability matrix below; Core, Capabilities, Rust, Vitest, and Playwright suites. |
| 6. New desktop experience | Proven | React/Tauri renders Task Timeline + Preview, approval/version decisions, lazy Developer Mode, chat, voice, capture, provider, Knowledge, execution, extension settings, and a dual-window Liquid Glass companion. Reduced Motion is enforced. | Production Playwright workspace, release, chat, voice, perception, Presence, Knowledge, execution-control, visual-regression, and extension workflows. |

## Public Contracts

| Contract | Status | Evidence |
| --- | --- | --- |
| Generated `CoreClient` groups for projects, conversations, tasks, approvals, versions, previews, artifacts, capabilities, and resumable events | Proven | `desktop/src/core/client.ts`, generated `api.d.ts`, and `client.test.ts`. |
| Local JSON-RPC and Cloud REST/SSE share application contracts | Proven | `core/tests/contracts`, `cloud/tests/test_http_contract.py`, generated OpenAPI drift gate. |
| Core injects identity, Version, path/network/Memory/execution authority and `scope_digest` | Proven | `assistant/test_contracts.py`, `assistant/test_tool_dispatch.py`, workspace, sandbox, MCP, Skill, and system-action scope tests. |
| EventEnvelope has UUIDv7 identity, global cursor, Task sequence, schema version, visibility, Scope IDs, time, and typed payload | Proven | `core/tests/test_contracts.py`, Outbox validation tests, generated contracts. |
| SSE supports resume and de-duplication | Proven | `cloud/tests/test_sync_api.py`, `desktop/src/core/events.test.ts`, and the live PostgreSQL two-device integration suite. |
| Standard error codes are stable | Proven | `PATH_OUT_OF_SCOPE`, `SCOPE_MISMATCH`, `APPROVAL_REQUIRED`, `SANDBOX_UNAVAILABLE`, `VERSION_CONFLICT`, `SECRET_EGRESS_BLOCKED`, and `WORKER_INTERRUPTED` are in `ErrorCode`, OpenAPI, and Cloud status mapping tests. |

## Required Test Plan

| Gate | Status | Evidence |
| --- | --- | --- |
| Illegal Task, Version, Changeset, Command, Runtime, and Preview transitions | Proven | Domain/state tests plus Hypothesis property matrices in `test_runtime_properties.py`, `test_memory_properties.py`, and command/domain suites. |
| Windows path security: traversal, junction/reparse, symlink, UNC, device path, ADS, case, and TOCTOU | Proven | `test_path_guard.py`, `test_runtime_security.py`, workspace revalidation tests, and Rust workspace/static Preview security tests. |
| Crash recovery without duplicate writes | Proven | Ledger, Unit of Work, assistant, memory, MCP, execution, Runtime, PostgreSQL, and OCI crash-point tests pass. |
| Observe/standard/autonomous permission matrix and explicit Active Version promotion | Proven | `test_policy.py`, `test_execution_settings.py`, system-action tests, and execution-control browser flow. |
| Local and cloud complete project loop, offline continuation, SSE resume, and two-device conflict | Proven | Local closure/browser workflows and live PostgreSQL Compose two-device tests pass. |
| Every old non-Legacy capability has a new black-box path | Proven | Capability matrix below and `docs/superpowers/plans/2026-07-11-assistant-capabilities.md`. No old runtime module or database is imported. |
| Playwright covers workspace, chat, approval, offline, conflict, HUD, Pet, extensions, voice, and Windows-scale layouts | Proven | Production Playwright suite; narrow and desktop screenshots include overflow/bounds assertions. |
| Performance: shell <=1.5s, Core <=3s, event-to-UI p95 <=100ms, initial gzip <=800KiB | Proven | `desktop/e2e/release.spec.ts` measures shell and event delivery; `scripts/release_performance.py` measures composed Core readiness and all initial Vite chunks. |

## Non-Legacy Capability Matrix

| Capability | Status | Primary evidence |
| --- | --- | --- |
| Durable scratch chat and project chat | Proven | Assistant ledger/application tests; `chat-workspace.spec.ts`; scratch workspace query tests. |
| Local/cloud OpenAI-compatible models, streaming, fallback, cancellation | Proven | `capabilities/tests/models/test_openai_compatible.py`, provider registry and assistant cancellation tests. Secret-free OpenRouter Nemotron/Hy3 profiles are validated by `test_settings.py`. |
| Web search/fetch and cited research | Proven | Capabilities web URL-guard/fixture tests; Core research Artifact tests; evidence RLS test exists for live PostgreSQL. |
| News | Proven | Information executor and Brave news fixture tests. |
| Weather | Proven | Open-Meteo adapter, geocoding ambiguity, units, and assistant dispatch tests. |
| Time and timezone | Proven | ZoneInfo/DST tests and `info.time` tool contract. |
| Maps | Proven | OpenStreetMap deep-link encoding and location resolution tests. |
| Stocks, FX, and crypto | Proven | Alpha Vantage, Frankfurter, market normalization, rate/error, and tool-schema tests. |
| Documents and RAG | Proven | TXT/Markdown/HTML/PDF/DOCX parsers, revisions, lexical results, deletion, Hermes non-mutation, live S3 object storage, and PostgreSQL RLS pass. |
| Hermes relational memory | Proven | Observation, Claim, revisions, Tombstones, policy, lexical projection, immutable Snapshot, degraded fallback, property tests, PostgreSQL generated `tsvector`, and RLS pass. |
| Screen understanding and game perception | Proven | Tauri display/window capture, explicit preview/attach, PNG/hash/scope checks, multimodal context tests, and `perception.spec.ts` using a Game window. |
| STT and streaming TTS | Proven | Core VoiceSession contracts, native loopback token, PCM Tauri Channel/AudioWorklet, sentence ordering, cancellation, packaged CosyVoice/TensorRT worker, and `voice.spec.ts` pass. Ten enforced real RTX 5060 Ti samples produced a 423.6ms first-frame p95, 0ms prefetched gap, and 15.2ms cancellation response. |
| Programmatic Fairy Pet | Proven | Dynamically and serially created Tauri `pet-render`/`pet-input` windows, a Rust coordinator, deterministic interaction phases, display-adaptive 15/30/60/144/300 runtime cadence, Three.js/WebGL2 SDF glass, procedural standard optics, real bounded-sampling WGC/D3D11/DirectComposition enhanced optics, Canvas fallback, reply/voice projections, revision-fenced preferences, atomic native placement, quick scratch chat, menus, and Reduced Motion pass unit, browser, HLSL compile, Rust, real native performance, window-alignment, click-through, and screenshot gates. The Pet has no CoreClient, project state, approval decision, general capture API, or execution API. |
| Slash Commands | Proven | Core-generated metadata and exact parser tests; natural-language keyword routing is statically forbidden. |
| Typed system actions | Proven | HTTPS URL, managed reveal path, clipboard, notification, fixed Settings, approval/idempotency journal, Rust protocol, and shell-shaped payload rejection tests. |
| Governed Fairy Skills | Proven | Strict immutable package loader, provenance/hash/schema tests, Registry integration, Scope-bound private Artifact, and permission tests. |
| Governed MCP | Proven | Official SDK stdio and Streamable HTTP tests, schema/trust/policy/approval/recovery, Cloud routes, tombstones, allowlists, desktop settings, and live PostgreSQL RLS integration pass. |

## Technology and Structure

- Desktop locks React 19.2.7, React Compiler 1.0.0, Vite 8.1.4,
  TanStack Router/Query, React Aria, Lucide, Three.js/WebGL2, Tauri 2.11.x,
  and a Rust workspace. Three.js is dynamically loaded only by `pet-render`.
- Python packages require CPython 3.13 with the standard GIL. Core remains
  transport-independent; FastAPI 0.139.x, Alembic, PostgreSQL drivers, S3, and
  provider adapters stay in Cloud/Capabilities.
- Docker composition pins PostgreSQL 18.4, SeaweedFS S3, mock OIDC, Node
  24.18.0, uv 0.11.28, pnpm 10.34.4, and Yarn 1.22.22. Services are non-root,
  capability-dropped, read-only, brokerless, and socket-free.
- `scripts/check_boundaries.py` rejects legacy/layer imports, secret-shaped
  literals, host shell use, browser speech, keyword routers, duplicate Memory
  authorities, privileged Renderer imports, Docker sockets/host namespaces,
  project-worker host binds, unowned source types, empty source directories,
  and oversized source modules.
- Boundary-checked Python and TypeScript/JavaScript modules remain below 1,200
  lines and CSS below 1,500 lines; generated contracts and Rust platform modules
  are exempt and are checked by their own compiler and test gates. Vendored
  crates under `desktop/src-tauri/crates` keep their upstream `LICENSE` and
  `UPSTREAM.md` provenance records, which the boundary gate accepts explicitly.

## Environment Evidence

- Docker Desktop server 29.6.1 ran PostgreSQL 18.4, S3, forced RLS, non-root
  OCI execution/Runtime, recovery, Outbox, and two-device integration: 27 passed.
- WSL2 FairySandbox attestation reported executor `wsl_fairy_sandbox` version
  `1.0.0`; structured execution completed with a verified stdout digest.
- The packaged native Voice Worker reported TensorRT/CUDA readiness on an RTX
  5060 Ti. `benchmark.py --samples 10 --enforce` passed with every first frame
  between 376.2ms and 423.6ms.
- Native WebView2 probes selected the RTX 5060 Ti for auto/high-performance and
  AMD Radeon integrated graphics for power-saving. Disabling GPU/WebGL selected
  the Canvas compatibility renderer without breaking pass-through, focus,
  topmost ordering, or tray lifetime.
- The 2026-07-14 baseline Shader completed separate 30-minute native soaks with 354 samples
  per GPU. NVIDIA D3D11 reported 0.054ms GPU-frame p95, 0.2ms CPU-frame p95,
  0.056% average single-core CPU, and 4.55MiB process-tree private-memory
  growth. AMD D3D11 reported 2.43ms GPU-frame p95, 0.2ms CPU-frame p95, 0.166%
  average single-core CPU, and 8.27MiB process-tree private-memory growth. Both
  sessions ended with the Liquid renderer healthy and running.

## Historical Fresh Release Evidence (2026-07-14 Baseline)

The final local gate ran after the cleanup and safety changes:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-all.ps1 -RequireWslSandbox
```

It exited successfully with:

- repository boundary, Ruff format, and Ruff lint gates passed;
- Sandbox Runner: 49 passed, 1 POSIX process-group case skipped on Windows;
- Core: 607 passed after the populated legacy SQLite migration regression was added;
- Capabilities: 104 passed;
- Cloud: 112 unit/contract tests and 27 live Docker integration tests passed;
- Alembic: offline upgrade from base through `20260712_0017` and full downgrade
  from head to base passed; deployment tests confirmed one linear head;
- local SQLite table rebuilds passed populated parent/child foreign-key fixtures,
  a production-shaped database-copy migration, `integrity_check`, and
  `foreign_key_check`;
- Rust: workspace format, Clippy with warnings denied, and all workspace tests
  passed;
- Desktop: 43 Vitest files / 173 tests and 50 production Playwright workflows
  passed;
- TypeScript and Vite production build passed;
- shell interactive and event-to-UI p95 browser tests passed their 1.5-second
  and 100ms limits;
- composed Core readiness was 1244.7ms against 3000ms;
- conservative initial renderer gzip was 394.0KiB against 800KiB;
- generated OpenAPI/TypeScript hashes were unchanged after regeneration; and
- `git diff --check` passed.

Docker/PostgreSQL/S3/OCI and the required real WSL attestation, structured
execution, projectless Workspace Runtime, and static Preview lifecycle all ran
as part of the command and passed.

## Historical Desktop Bundle Evidence (2026-07-14 Baseline)

The final Windows release rebuild compiled the optimized Tauri application,
bundled the migration-fixed Core sidecar, pinned MinGit, and the native
CosyVoice/CUDA worker, then produced a split WiX release.
`Fairy_0.2.0_x64_en-US.msi` is 2,055,739 bytes with SHA-256
`C3DFEB1195339921175A9D89752C90A559E61FDAEAA50A0FACFBFC3100BF6096`.
Six external cabinets bring the complete release to 3,800,443,736 bytes;
every file is listed with its SHA-256 in `release-manifest.json` and no cabinet
exceeds the Windows Installer media limit.

A real non-elevated per-user cycle installed the preserved 0.1.0 MSI, upgraded
it to 0.2.0 with the same UpgradeCode, verified registration and PE metadata,
confirmed a changed application hash, ran the native Liquid Glass probe from
the installed path, and uninstalled cleanly. An elevated release host uses the
same package's default per-machine scope.

The release-specific gates are reproducible with:

```powershell
Set-Location desktop
npm run release:windows
Set-Location ..
powershell -File scripts\test-presence-native.ps1 -GpuPreference high_performance
powershell -File scripts\test-presence-native.ps1 -GpuPreference power_saving
powershell -File scripts\test-presence-native.ps1 -ExpectedMode compatibility
powershell -File scripts\test-presence-soak.ps1 -DurationMinutes 30 -GpuPreference high_performance -ExpectedGpuPattern NVIDIA
powershell -File scripts\test-presence-soak.ps1 -DurationMinutes 30 -GpuPreference power_saving -ExpectedGpuPattern AMD
powershell -File scripts\test-presence-install-cycle.ps1 -BaselineInstaller 'C:\path\Fairy_0.1.0_x64_en-US.msi'
```

## Historical Git Evidence (2026-07-14 Baseline)

`git diff --check` passes. The twelve Liquid Glass implementation tasks were
independently verified and committed on `codex/fairy-v3`; the final visual and
release certification is prepared as one isolated commit after all
environment-backed gates passed.

The authoritative release command is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-all.ps1 -RequireWslSandbox
```

The command requires Docker Desktop and the installed WSL2 FairySandbox. Both
environment gates must pass before claiming a production deployment is fully
validated.
