# Fairy V3 Completion Audit

- Audit date: 2026-07-13
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

## Acceptance Summary

Fairy V3 is functionally source-complete against the approved plan. Local,
contract, migration, Rust, renderer, production-browser, Docker Compose, and
real WSL2 FairySandbox gates are proven. The native CosyVoice/TensorRT worker
also passes its real RTX 5060 Ti performance gate: first-frame p95 is 423.6ms,
prefetched gap is 0ms, and cancellation response is 15.2ms.

## Implementation Changes

| Requirement | Status | Implementation | Executable evidence |
| --- | --- | --- | --- |
| 1. Core Foundation | Proven | `core/src/fairy_core/domain`, `application`, `commanding`, `storage`, `runtime`, `memory`, `workspace`, and typed contracts define Project, Conversation, Task, Version, Workspace, Changeset, Approval, CommandRun/Event, Runtime, Preview, Artifact, Checkpoint, and Hermes Memory. | Core full suite: 582 passed; state, execution, storage, memory, workspace, and contract suites. |
| 2. Command, permission, and recovery authority | Proven | One thread-safe `ToolRegistry` generates Agent tools, capability manifest, policy metadata, and Slash availability. Device/cloud profiles and toggles are Core-owned. All model-visible schemas are closed. | `test_policy.py`, `test_execution_settings.py`, `test_command_bus.py`, `test_ledger.py`, `test_persistence_recovery.py`, and assistant approval/recovery tests. |
| 3. Local project closure | Proven | Imported sources are copied to managed Git; each Task gets an isolated worktree. Changesets, dependency templates, Review, Preview, Checkpoint, accept, and discard are Core-orchestrated. WSL execution requires attestation and has no host fallback. | Local/Rust closure suites plus real FairySandbox 1.0.0 attestation and structured WSL execution passed. |
| 4. Cloud and multi-device | Proven | PostgreSQL schema/leases/outbox, S3 object adapter, REST/SSE, optimistic Project revision, candidate conflict retention, execution worker, Runtime worker, and private Preview gateway are implemented. No Redis or NATS is present. | Cloud unit/contract suite: 110 passed; Alembic full upgrade/downgrade; 27 live PostgreSQL 18.4/S3/RLS/OCI/two-device integration tests passed in Docker. |
| 5. Complete non-Legacy capabilities | Proven | Scratch/project assistant, local/cloud provider profiles, governed web/research/information, documents/RAG, Hermes, perception, voice, Presence/Pet, Slash Commands, typed system actions, Skills, and MCP all use Core contracts. | Capability matrix below; Core, Capabilities, Rust, Vitest, and Playwright suites. |
| 6. New desktop experience | Proven | React/Tauri renders Task Timeline + Preview, Context/telemetry state, approval/version decisions, lazy Developer Mode, chat, voice, capture, Presence/Pet, provider, Knowledge, execution, and extension settings. Reduced Motion is enforced. | Production Playwright workspace, release, chat, voice, perception, Presence, Knowledge, execution-control, and extension workflows. |

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
| Programmatic Fairy Pet | Proven | Canvas nonblank/scaling tests, public-event projection tests, typed cross-window requests, revision-fenced pet preferences, anchored native resizing, quick scratch chat, context menu, Reduced Motion, and `presence.spec.ts`. The Pet has no CoreClient, project state, approval decision, capture, or execution API. |
| Slash Commands | Proven | Core-generated metadata and exact parser tests; natural-language keyword routing is statically forbidden. |
| Typed system actions | Proven | HTTPS URL, managed reveal path, clipboard, notification, fixed Settings, approval/idempotency journal, Rust protocol, and shell-shaped payload rejection tests. |
| Governed Fairy Skills | Proven | Strict immutable package loader, provenance/hash/schema tests, Registry integration, Scope-bound private Artifact, and permission tests. |
| Governed MCP | Proven | Official SDK stdio and Streamable HTTP tests, schema/trust/policy/approval/recovery, Cloud routes, tombstones, allowlists, desktop settings, and live PostgreSQL RLS integration pass. |

## Technology and Structure

- Desktop locks React 19.2.7, React Compiler 1.0.0, Vite 8.1.4,
  TanStack Router/Query, React Aria, Lucide, Tauri 2.11.x, and a Rust workspace.
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
- Source modules remain below 1,200 lines and CSS below 1,500 lines; generated
  contracts are exempt and are checked by deterministic regeneration.

## Environment Evidence

- Docker Desktop server 29.6.1 ran PostgreSQL 18.4, S3, forced RLS, non-root
  OCI execution/Runtime, recovery, Outbox, and two-device integration: 27 passed.
- WSL2 FairySandbox attestation reported executor `wsl_fairy_sandbox` version
  `1.0.0`; structured execution completed with a verified stdout digest.
- The packaged native Voice Worker reported TensorRT/CUDA readiness on an RTX
  5060 Ti. `benchmark.py --samples 10 --enforce` passed with every first frame
  between 376.2ms and 423.6ms.

## Fresh Release Evidence

The final local gate ran after the cleanup and safety changes:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-all.ps1
```

It exited successfully with:

- repository boundary, Ruff format, and Ruff lint gates passed;
- Sandbox Runner: 45 passed, 1 POSIX process-group case skipped on Windows;
- Core: 582 passed;
- Capabilities: 104 passed;
- Cloud: 110 unit/contract tests and 27 live Docker integration tests passed;
- Alembic: offline upgrade from base through `20260712_0017` and full downgrade
  from head to base passed; deployment tests confirmed one linear head;
- Rust: workspace format, Clippy with warnings denied, and all workspace tests
  passed;
- Desktop: 26 Vitest files / 107 tests and 27 production Playwright workflows
  passed;
- TypeScript and Vite production build passed;
- shell interactive and event-to-UI p95 browser tests passed their 1.5-second
  and 100ms limits;
- composed Core readiness was 1131.6ms against 3000ms;
- conservative initial renderer gzip was 238.6KiB across 21 files against
  800KiB;
- generated OpenAPI/TypeScript hashes were unchanged after regeneration; and
- `git diff --check` passed.

Docker/PostgreSQL/S3/OCI ran as part of the command and passed. The default
script printed its WSL skip; the real attestation and structured execution
were then run separately and passed.

## Desktop Bundle Evidence

`npm run tauri build` completed after the Windows bundle icon was made
explicit. It compiled the optimized Tauri application, bundled the Core
sidecar and pinned MinGit runtime, and produced both Windows installers:

- `Fairy_0.1.0_x64_en-US.msi`: 77,573,739 bytes, SHA-256
  `9025E12D9E2CF8C4C51638CB47ED28177675FA38B082E7B04F604E23BA8BDF41`;
- `Fairy_0.1.0_x64-setup.exe`: 61,652,022 bytes, SHA-256
  `BF21D7252F3906D28E5462D1ECD9AE9FD23A547A8B89D7CAF5C474891C3E6B44`.

The first bundle attempt correctly failed because Tauri could not resolve a
Windows `.ico`; the explicit `icons/icon.ico` bundle configuration fixed that
release-only defect. Installer artifacts remain ignored build outputs.

## Git Evidence

`git diff --check` passes. Tasks 50 through 56 were each independently staged,
verified, and committed on `codex/fairy-v3`; the earlier worktree index-lock
restriction no longer reproduces. Task 57 records the final audit and release
artifacts after its environment-backed gates.

The authoritative release command is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-all.ps1 -SkipDocker
```

Run without `-SkipDocker` on a Docker-enabled release host, and add
`-RequireWslSandbox` on a Windows host with the installed FairySandbox. Those
environment gates must pass before claiming a production deployment is fully
validated.
