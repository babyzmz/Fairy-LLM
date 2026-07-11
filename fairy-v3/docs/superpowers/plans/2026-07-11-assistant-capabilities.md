# Fairy V3 Assistant Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Fairy V3 as a Windows-first desktop assistant with Task-bound chat, local/cloud models, governed tools, research, information, documents/RAG, voice, perception, Presence, and typed system actions.

**Architecture:** `fairy-core` owns durable state machines, ports, policy, Scope, and contracts. A new `fairy-capabilities` package owns replaceable provider and parser adapters and composes local/cloud services. React and Tauri consume generated contracts; all effects remain behind Core Commands or narrowly typed host APIs.

**Tech Stack:** Python 3.13, Pydantic 2, SQLAlchemy 2, FastAPI 0.139, PostgreSQL 18, SQLite, httpx, pypdf, python-docx, React 19.2, Tauri 2, Rust, Vitest, Playwright, pytest/Hypothesis, Docker Compose.

## Global Constraints

- Preserve Project-first, Task-driven, Preview-first behavior and immutable Scope injection.
- Every assistant turn creates or binds a Task before model or tool execution.
- All effects enter the Command Bus; no renderer, model, Pet, or Agent bypass is permitted.
- `fairy-core` continues to depend only on Pydantic and SQLAlchemy.
- Hermes relational data remains canonical memory; document RAG is a separate corpus and disposable projection.
- Companion and Presence never call an LLM, own project state, approve, or execute.
- Generic local execution requires a healthy WSL2 FairySandbox and never falls back to a host shell.
- Provider secrets remain outside Core persistence, contracts, events, logs, prompts, and artifacts.
- Each Task below uses test-first red/green/refactor, runs its package gate, and ends in one independent commit.
- This plan is executed inline because the user explicitly prohibited subagents.

---

### Task 29: Durable Messages, Turns, and Tool Invocations

**Files:**
- Create: `core/src/fairy_core/assistant/models.py`
- Create: `core/src/fairy_core/assistant/ports.py`
- Create: `core/src/fairy_core/assistant/repository.py`
- Create: `core/src/fairy_core/assistant/ledger.py`
- Create: `core/src/fairy_core/assistant/__init__.py`
- Modify: `core/src/fairy_core/storage/schema.py`
- Modify: `core/src/fairy_core/persistence/unit_of_work.py`
- Modify: `core/src/fairy_core/contracts/models.py`
- Modify: `core/src/fairy_core/contracts/methods.py`
- Modify: `core/src/fairy_core/application/service.py`
- Modify: `cloud/src/fairy_cloud/api.py`
- Create: `cloud/migrations/versions/20260711_0008_assistant_ledger.py`
- Modify: `desktop/src/core/contracts.ts`
- Modify: `desktop/src/core/client.ts`
- Modify: `desktop/src/core/cloudTransport.ts`
- Test: `core/tests/assistant/test_models.py`
- Test: `core/tests/assistant/test_repository_contract.py`
- Test: `core/tests/assistant/test_contracts.py`
- Test: `cloud/tests/integration/test_assistant_rls.py`

**Interfaces:**
- Produces `Message`, `AssistantTurn`, `ToolInvocation`, their status enums, and tenant-scoped repository methods.
- Produces public methods `messages.list`, `assistant.turns.create`, `assistant.turns.get`, and `assistant.turns.cancel`.
- `AssistantTurn.create(task, profile_id, scope)` copies `scope_digest`, Memory Snapshot ID/hash, and never accepts these fields from transport input.

- [x] Write model tests proving immutable Message ordering, legal Turn transitions, terminal cancellation, Tool Invocation sequence uniqueness, and canonical argument hashing.
- [x] Run `uv run --project core pytest tests/assistant/test_models.py -q` and confirm failures are caused by missing assistant types.
- [x] Implement the domain models and transition guards with UUIDv7 IDs and UTC timestamps.
- [x] Write repository contract tests for append/list/get, idempotent Turn creation, Conversation isolation, and recovery of orphaned running Turns as `WORKER_INTERRUPTED`.
- [x] Run the repository tests and confirm the missing tables/repository fail before implementing SQLAlchemy rows and adapters.
- [x] Add SQLite and PostgreSQL tables with tenant/Conversation/Task foreign keys, unique sequence constraints, RLS policy inclusion, and no cascade that could erase ledger history.
- [x] Add request/response models and the four public Core methods; verify generated OpenAPI rejects client-provided Scope or Memory IDs.
- [x] Run `uv run pytest tests/assistant -q` and `uv run --project cloud pytest -m "not integration" -q`.
- [x] Run offline Alembic upgrade/downgrade and contract generation.
- [x] Commit with `feat(v3): add durable assistant ledger`.

Verification note: the full local gate passed with Docker explicitly skipped.
The real PostgreSQL/RLS test is collected in the Compose integration profile;
it was not executed because this machine has no discoverable Docker CLI.

### Task 30: Provider Profiles, Streaming, Cancellation, and Secret Boundary

**Files:**
- Create: `core/src/fairy_core/providers/models.py`
- Create: `core/src/fairy_core/providers/ports.py`
- Create: `core/src/fairy_core/providers/registry.py`
- Create: `core/src/fairy_core/providers/__init__.py`
- Create: `capabilities/pyproject.toml`
- Create: `capabilities/src/fairy_capabilities/models/openai_compatible.py`
- Create: `capabilities/src/fairy_capabilities/settings.py`
- Create: `capabilities/src/fairy_capabilities/composition.py`
- Create: `capabilities/src/fairy_capabilities/stdio.py`
- Create: `capabilities/tests/models/test_openai_compatible.py`
- Modify: `desktop/src-tauri/crates/core-bridge/src/lib.rs`
- Modify: `desktop/src-tauri/src/lib.rs`
- Modify: `scripts/test-all.ps1`
- Modify: `scripts/check_boundaries.py`

**Interfaces:**
- `ModelProvider.stream(request: ModelRequest, cancellation: CancellationToken) -> Iterator[ModelDelta]`.
- `ProviderRegistry.list_public()` returns non-secret profiles and capability health.
- `ProviderSecretResolver.resolve(reference: str) -> SecretValue` is implemented only in composition; `SecretValue` redacts `repr` and string conversion.
- Tauri launches module `FAIRY_CORE_MODULE`, defaulting to `fairy_capabilities.stdio` when the composed package is available.

- [x] Write Core tests for profile validation, explicit fallback compatibility, cancellation state, redacted secret values, and modality negotiation.
- [x] Run the tests and confirm missing provider ports fail.
- [x] Implement provider-neutral frozen types and registry rules in Core.
- [x] Write local HTTP fixture tests for OpenAI-compatible SSE text deltas, tool candidates, usage, malformed frames, timeout, upstream error, fallback, and mid-stream cancellation.
- [x] Run the fixture tests and confirm the adapter is missing.
- [x] Create `fairy-capabilities`, lock exact dependencies, and implement the adapter without logging headers or request bodies containing secrets.
- [x] Add provider-free `fairy_core.transports.stdio` and provider-composed `fairy_capabilities.stdio` launch tests.
- [x] Update the Tauri launch spec to inject only configured credential references and to support the composed module without returning secrets to JavaScript.
- [x] Add capabilities lint/tests/lock checks to `scripts/test-all.ps1` and dependency-direction assertions to `check_boundaries.py`.
- [x] Run Core, capabilities, Rust bridge tests, boundary checks, and contract generation.
- [x] Commit with `feat(v3): add model provider composition`.

Verification note: the OpenAI-compatible adapter completed a real SSE request
against the zero-price OpenRouter model `cohere/north-mini-code:free`; the API
key remained in an external file and process memory only. The complete local
gate passed with 340 Core, 13 Capabilities, 51 Cloud unit, all Rust, 21 Vitest,
and 2 Playwright tests, plus TypeScript/build and offline migration checks.
The repository Docker Compose environment includes Capabilities composition,
but real PostgreSQL/S3 integration remained explicitly skipped because this
machine still has no discoverable Docker CLI/daemon.

### Task 31: Model-led Assistant Loop and Command Bus Dispatch

**Files:**
- Create: `core/src/fairy_core/assistant/application.py`
- Create: `core/src/fairy_core/assistant/context.py`
- Create: `core/src/fairy_core/assistant/tools.py`
- Modify: `core/src/fairy_core/commanding/registry.py`
- Modify: `core/src/fairy_core/application/service.py`
- Modify: `core/src/fairy_core/contracts/models.py`
- Modify: `core/src/fairy_core/contracts/methods.py`
- Modify: `capabilities/src/fairy_capabilities/composition.py`
- Test: `core/tests/assistant/test_application.py`
- Test: `core/tests/assistant/test_tool_dispatch.py`
- Test: `core/tests/contracts/test_assistant_transport_contract.py`

**Interfaces:**
- `AssistantApplication.run_turn(turn_id, cancellation) -> AssistantTurn` performs at most three model rounds and eight tools.
- `ToolExecutor.execute(definition, scope, arguments) -> ToolResult` receives only Core-injected Scope.
- `assistant.turns.run` returns terminal Turn metadata while text is read through durable events and `messages.list`.

- [x] Write a failing end-to-end Core test where a scratch user request creates a Task, immutable Hermes Snapshot, user Message, Turn, deltas, final Message, and completion event.
- [x] Write failing tests proving `direct_answer` always exists, ordinary chat performs no tool call, no keyword forces a route, client/model Scope fields are discarded, repeated tool arguments are rejected, and cancellation drops later deltas.
- [x] Run the assistant tests and verify the application is absent.
- [x] Implement bounded context assembly from Conversation Messages, the Task-bound Hermes Snapshot, provider capability manifest, and source-labelled attachments.
- [x] Implement the model loop, durable delta sequence, tool candidate validation, CommandRun lifecycle, approval result, bounded ToolResult, final Message commit, and interrupted recovery.
- [x] Add public run/retry methods and consistent JSON-RPC/REST error mapping.
- [x] Run Core assistant/command/recovery tests, Cloud transport contract tests, contract generation, and Ruff.
- [x] Commit with `feat(v3): run task-bound assistant turns`.

Verification note: the complete local gate passed with 357 Core, 14
Capabilities, 51 Cloud unit, all Rust, 21 Vitest, and 2 Playwright tests.
Contracts regenerated without drift and the desktop entry bundle was 102.66 KB
gzip. Docker/PostgreSQL and WSL sandbox verification remained explicitly
skipped because neither runtime was discoverable on this machine.

### Task 32: Safe Web Research and Evidence Artifacts

**Files:**
- Create: `core/src/fairy_core/research/models.py`
- Create: `core/src/fairy_core/research/ports.py`
- Create: `core/src/fairy_core/research/application.py`
- Create: `capabilities/src/fairy_capabilities/web/url_guard.py`
- Create: `capabilities/src/fairy_capabilities/web/fetch.py`
- Create: `capabilities/src/fairy_capabilities/web/brave.py`
- Create: `capabilities/src/fairy_capabilities/web/tools.py`
- Modify: `core/src/fairy_core/commanding/registry.py`
- Modify: `core/src/fairy_core/storage/schema.py`
- Modify: `cloud/src/fairy_cloud/storage/schema.py`
- Create: `cloud/migrations/versions/20260711_0009_research_evidence.py`
- Test: `capabilities/tests/web/test_url_guard.py`
- Test: `capabilities/tests/web/test_fetch.py`
- Test: `capabilities/tests/web/test_brave.py`
- Test: `core/tests/research/test_application.py`

**Interfaces:**
- `SearchPort.search(SearchRequest) -> tuple[SearchHit, ...]`.
- `FetchPort.fetch(FetchRequest) -> FetchedDocument` returns final URL, textual media type, bytes hash, title, and bounded normalized text.
- `ResearchApplication.build(task_id, kind, question, sources) -> Artifact` supports `web_brief`, `specs`, `compare`, and `release`.

- [x] Write failing URL tests for private IPv4/IPv6, encoded hosts, credentials, non-HTTP schemes, redirect loops, public-to-private redirects, DNS answer changes, oversized bodies, binary media, and decompression limits.
- [x] Implement canonical URL parsing, pre-connect and post-connect address validation, redirect-by-redirect checks, media allowlist, byte/time limits, and cache keys.
- [x] Write and run failing Brave fixture tests for auth, paging, freshness, provider diagnostics, malformed payloads, and no-key unavailable health.
- [x] Implement Brave search/news adapters with credential references and deterministic normalized hits.
- [x] Write failing research tests for citation retention, duplicate canonical URLs, source hashes, unsupported artifact kind, injection-labelled excerpts, and transactional Artifact/Event creation.
- [x] Implement Evidence persistence and research synthesis input/output without treating fetched text as instructions.
- [x] Register `web.search`, `web.fetch`, and `research.build` Tool Definitions and execute them only through Command Bus dispatch.
- [x] Run capabilities web tests, Core research tests, Cloud migration checks, and boundary checks.
- [x] Commit with `feat(v3): add governed web research`.

Verification note: the complete local gate passed with 386 Core, 55
Capabilities, 52 Cloud unit, all Rust, 21 Vitest, and 2 Playwright tests.
URL/DNS/redirect/decompression limits, exact CommandRun binding, Scope network
policy, transactional Evidence persistence, offline migration reversal, and
generated contract stability are covered. The entry bundle remained 102.66 KB
gzip. The PostgreSQL RLS test is collected by the Compose integration profile
but was not executed because no Docker CLI/daemon is discoverable; WSL is also
not installed, so sandbox attestation remained explicitly skipped.

### Task 33: News, Weather, Time, Maps, Stocks, FX, and Crypto

**Files:**
- Create: `core/src/fairy_core/information/models.py`
- Create: `capabilities/src/fairy_capabilities/information/open_meteo.py`
- Create: `capabilities/src/fairy_capabilities/information/timezones.py`
- Create: `capabilities/src/fairy_capabilities/information/frankfurter.py`
- Create: `capabilities/src/fairy_capabilities/information/alpha_vantage.py`
- Create: `capabilities/src/fairy_capabilities/information/tools.py`
- Modify: `core/src/fairy_core/commanding/registry.py`
- Modify: `capabilities/src/fairy_capabilities/composition.py`
- Test: `capabilities/tests/information/test_open_meteo.py`
- Test: `capabilities/tests/information/test_timezones.py`
- Test: `capabilities/tests/information/test_frankfurter.py`
- Test: `capabilities/tests/information/test_alpha_vantage.py`

**Interfaces:**
- Tool names are `info.weather`, `info.news`, `info.time`, `info.map`, `info.stock`, `info.fx`, and `info.crypto`.
- Every result has provider, `observed_at`, freshness label, normalized values, source URL, and diagnostics; unavailable credentials produce `CAPABILITY_NOT_AVAILABLE`.

- [x] Write fixture tests for geocoding ambiguity, weather units, timezone conversion across DST, OpenStreetMap deep-link encoding, exchange-rate dates, delayed stock quotes, crypto market currency, rate limits, and provider error payloads.
- [x] Run tests and confirm adapters are missing.
- [x] Implement strict Pydantic result models and adapters with injectable endpoints/clocks and bounded retries for idempotent GET requests.
- [x] Add Tool Definitions, policy risk, network capability metadata, provider health, and normalized public summaries.
- [x] Add assistant-loop tests proving the model selects tools from schemas and ordinary chat still uses `direct_answer` without keyword routing.
- [x] Run all capabilities information tests, Core assistant tests, Ruff, and contract generation.
- [x] Commit with `feat(v3): add live information tools`.

Verification note: fixture-backed adapters use current Open-Meteo endpoints,
Frankfurter v2, Alpha Vantage, Brave News, OpenStreetMap, and locked IANA
`tzdata` 2026.3. The package gate passed with 390 Core, 78 Capabilities, and
52 Cloud unit tests; generated contracts remained stable. Remote information
tools enforce the Core network policy while time conversion and map-link
generation remain available offline. Provider secrets are reference-only and
the desktop environment boundary forwards only `FAIRY_PROVIDER_*` values.

### Task 34: Managed Documents and RAG Projection

**Files:**
- Create: `core/src/fairy_core/documents/models.py`
- Create: `core/src/fairy_core/documents/ports.py`
- Create: `core/src/fairy_core/documents/application.py`
- Create: `core/src/fairy_core/documents/repository.py`
- Create: `core/src/fairy_core/documents/search.py`
- Create: `capabilities/src/fairy_capabilities/documents/extract.py`
- Create: `capabilities/src/fairy_capabilities/documents/parsers.py`
- Modify: `core/src/fairy_core/storage/schema.py`
- Modify: `core/src/fairy_core/persistence/unit_of_work.py`
- Modify: `core/src/fairy_core/contracts/models.py`
- Modify: `core/src/fairy_core/contracts/methods.py`
- Modify: `cloud/src/fairy_cloud/storage/schema.py`
- Create: `cloud/migrations/versions/20260711_0010_documents.py`
- Test: `core/tests/documents/test_application.py`
- Test: `core/tests/documents/test_search.py`
- Test: `capabilities/tests/documents/test_parsers.py`
- Test: `cloud/tests/integration/test_document_rls.py`

**Interfaces:**
- `documents.import`, `documents.list`, `documents.get`, `documents.search`, and `documents.delete` are Task-scoped Core methods.
- Parser output is `ExtractedDocument(media_type, parser, parser_version, sections)`; chunks contain canonical revision/hash/locator provenance.
- No document method calls a Hermes mutation method.

- [x] Write parser tests using minimal TXT, Markdown, HTML, PDF, and DOCX fixtures plus encrypted, malformed, oversized, and unsupported input.
- [x] Run parser tests and verify failure before adding pypdf/python-docx adapters.
- [x] Write Core tests for explicit import approval, managed copy hash, immutable revisions, deterministic chunks, lexical results, tenant/Project/Conversation isolation, deletion, and stale projection rejection.
- [x] Add a regression test that monkeypatches every Hermes mutation method to fail if RAG calls it.
- [x] Implement document records, repository, deterministic chunker, lexical projection, canonical-result revalidation, and Command Bus methods.
- [x] Add PostgreSQL tables, RLS, generated `tsvector` index, migration, and Docker integration tests.
- [x] Run document suites, Hermes suites, migrations, contracts, and boundary checks.
- [x] Commit with `feat(v3): add managed document rag`.

Task 34 implements separate canonical Document, immutable Revision, and lexical Chunk
tables without mutating Hermes. Local blobs are content-addressed below Fairy's
managed directory; cloud blobs use a tenant-bound S3 adapter. Core, Capabilities,
Cloud unit, desktop contract, migration SQL, and boundary gates pass. The PostgreSQL
RLS/search and S3 integration tests collect successfully but were not executed on
this workstation because neither the Docker CLI nor Docker Desktop is installed.

### Task 35: Scratch and Project Chat Desktop Experience

**Files:**
- Create: `desktop/src/chat/ChatWorkspace.tsx`
- Create: `desktop/src/chat/MessageList.tsx`
- Create: `desktop/src/chat/Composer.tsx`
- Create: `desktop/src/chat/useAssistantTurn.ts`
- Create: `desktop/src/chat/slashCommands.ts`
- Create: `desktop/src/settings/ProviderSettings.tsx`
- Modify: `desktop/src/app/WorkspaceShell.tsx`
- Modify: `desktop/src/app/workspace.css`
- Modify: `desktop/src/core/client.ts`
- Test: `desktop/src/chat/ChatWorkspace.test.tsx`
- Test: `desktop/src/chat/slashCommands.test.ts`
- Test: `desktop/e2e/chat.spec.ts`

**Interfaces:**
- CoreClient adds typed `messages`, `assistant.turns`, `providers`, `documents`, and event-delta helpers generated from the shared contract.
- Slash Commands are `/new`, `/project`, `/permission`, `/stop`, `/clear`, and `/help`; parsing occurs only when the first non-space character is `/`.

- [x] Write failing component tests for scratch creation, project binding, sending, stream resume/de-duplication, cancellation, retry, provider unavailable, approval, offline state, and keyboard/focus behavior.
- [x] Write failing slash tests proving exact command parsing and that natural-language weather/news phrases remain ordinary user text.
- [x] Implement compact Chat/Project tabs, Message list, accessible Composer controls, status indicators, attachment controls, and Developer Mode details without nested cards.
- [x] Wire events by global cursor and Turn chunk index; reconnect without duplicating visible text and load final Messages after completion.
- [x] Add Provider settings showing non-secret profile health and secret presence only; no secret value may be rendered or read back.
- [x] Add Playwright desktop/mobile-scale/reduced-motion flows using the real JSON-RPC fixture.
- [x] Run Vitest, Playwright at 880x680 and 640x700, TypeScript, build, and gzip size gate.
- [x] Commit with `feat(v3): add task-bound desktop chat`.

Verification note: the complete local gate passed with 401 Core, 92 Capabilities,
55 Cloud unit, all Rust, 36 Vitest, and 6 Playwright tests. Generated contracts
remained stable and the desktop entry bundle was 109.09 KB gzip. Screenshot review
at 880x680 and 640x700 found no overlap or overflow, and transient cloud SSE
reconnection retains `Last-Event-ID` without duplicating chunks. Docker/PostgreSQL,
S3, and WSL verification remained explicitly skipped because those runtimes were
not discoverable on this machine.

### Task 36: STT, TTS, WAV Contract, and Sentence Queue

**Files:**
- Create: `core/src/fairy_core/voice/models.py`
- Create: `core/src/fairy_core/voice/ports.py`
- Create: `capabilities/src/fairy_capabilities/voice/openai_audio.py`
- Create: `desktop/src/voice/sentenceQueue.ts`
- Create: `desktop/src/voice/VoiceController.tsx`
- Modify: `core/src/fairy_core/contracts/models.py`
- Modify: `core/src/fairy_core/contracts/methods.py`
- Modify: `core/src/fairy_core/application/service.py`
- Test: `capabilities/tests/voice/test_openai_audio.py`
- Test: `desktop/src/voice/sentenceQueue.test.ts`
- Test: `desktop/src/voice/VoiceController.test.tsx`
- Test: `desktop/e2e/voice.spec.ts`

**Interfaces:**
- `voice.transcribe` accepts bounded base64 audio and returns ordered transcript segments.
- `voice.synthesize` accepts a public assistant Message/Turn reference and returns base64 PCM WAV plus sample rate/channels/frames/hash.
- `SentenceQueue.push(turnId, delta)` emits only complete sentence chunks; `cancel(turnId)` aborts fetch/playback and clears duplicates.

- [x] Write failing adapter tests for multipart transcription, WAV RIFF validation, wrong media type, upstream cancellation, size bounds, timeout, and unavailable profile.
- [x] Implement provider-neutral voice types and OpenAI-compatible audio adapter with no browser synthesis fallback.
- [x] Write failing queue tests for multilingual punctuation, abbreviations, final remainder, ordering, duplicate suppression, cancellation, and new-recording interruption.
- [x] Implement the sentence queue and controller using MediaRecorder and Audio playback of validated WAV blobs.
- [x] Add accessible record/stop/speak controls and explicit denied/unavailable device states.
- [x] Run Core contracts, capabilities voice tests, Vitest, Playwright, build, and boundary checks.
- [x] Commit with `feat(v3): add governed voice pipeline`.

Task 36 verification: the complete available gate passed with 410 Core tests,
99 capability-adapter tests, 55 cloud unit tests, the full Rust workspace, 47
Vitest tests, and 9 Playwright workflows. TTS accepts only ledger-bound ranges
from completed public assistant messages, validates RIFF PCM metadata and SHA-256
before playback, and aborts cloud fetches on cancellation. Generated contracts
remained stable and the desktop entry bundle was 113.05 KB gzip. Docker/PostgreSQL,
S3, and WSL verification remained explicitly skipped because those runtimes were
not discoverable on this machine.

### Task 37: Screen and Game Perception

**Files:**
- Create: `desktop/src-tauri/src/capture.rs`
- Create: `desktop/src/perception/CaptureControl.tsx`
- Create: `core/src/fairy_core/perception/models.py`
- Modify: `desktop/src-tauri/Cargo.toml`
- Modify: `desktop/src-tauri/src/lib.rs`
- Modify: `desktop/src-tauri/capabilities/main.json`
- Modify: `core/src/fairy_core/providers/models.py`
- Modify: `desktop/src/chat/Composer.tsx`
- Test: `desktop/src-tauri/tests/capture_scope.rs`
- Test: `desktop/src/perception/CaptureControl.test.tsx`
- Test: `core/tests/providers/test_multimodal_context.py`
- Test: `desktop/e2e/perception.spec.ts`

**Interfaces:**
- Tauri `capture_surface(CaptureRequest) -> CaptureResult` is authorized only for `main` and returns bounded PNG data, dimensions, source label, and timestamp.
- Core `ImageAttachment` binds Task ID, media hash, dimensions, explicit persistence choice, and untrusted-data label.

- [ ] Write failing Rust tests for window authorization, invalid display/window IDs, pixel/byte bounds, and denial from Presence/Guide.
- [ ] Implement user-triggered display/window enumeration and capture with no pointer, keyboard, click, or process API.
- [ ] Write failing Core tests for vision-capability negotiation, Task/Scope binding, ephemeral image cleanup, attachment hash mismatch, and screen-text injection labelling.
- [ ] Implement multimodal request attachment assembly and zero ephemeral buffers after provider completion/cancellation.
- [ ] Add capture controls with a visible source preview, explicit attach/discard action, and no background watcher.
- [ ] Run Cargo fmt/clippy/tests, Core tests, Vitest, Playwright, and Windows build.
- [ ] Commit with `feat(v3): add user-triggered screen perception`.

### Task 38: Presence, Pet, and Guide Windows

**Files:**
- Create: `desktop/src/presence/PresenceApp.tsx`
- Create: `desktop/src/presence/projection.ts`
- Create: `desktop/src/presence/persistence.ts`
- Create: `desktop/src/presence/presence.css`
- Create: `desktop/src/guide/GuideApp.tsx`
- Modify: `desktop/src/app/App.tsx`
- Modify: `desktop/src-tauri/tauri.conf.json`
- Modify: `desktop/src-tauri/src/lib.rs`
- Modify: `desktop/src-tauri/capabilities/pet.json`
- Create: `desktop/src-tauri/capabilities/guide.json`
- Modify: `resources/manifest.json`
- Test: `desktop/src/presence/projection.test.ts`
- Test: `desktop/src/presence/PresenceApp.test.tsx`
- Test: `desktop/src-tauri/tests/window_scope.rs`
- Test: `desktop/e2e/presence.spec.ts`

**Interfaces:**
- `PresenceProjection.reduce(publicEvent)` maps only durable user-visible event types to ambient/work states.
- Presence local settings contain monitor-relative position, scale, Quiet Mode, dismissed notice IDs, and reduced-motion override; no domain entity or model context is stored.

- [ ] Inventory approved legacy character media, record source/hash/license assertion/intended use, and copy only manifested assets.
- [ ] Write failing projection tests proving internal/developer events, model deltas, tool arguments, and hidden reasoning cannot become Presence activity.
- [ ] Write failing interaction tests for hover input, 5 px drag threshold, 20 px snap, per-monitor restore, Quiet Mode, dismissible notices, closable reply bubble, AFK/density, and reduced motion.
- [ ] Implement one owned Presence boundary and a display-only click-through Guide boundary.
- [ ] Configure strict window capabilities: main owns Core/capture/actions; Presence owns local projection/settings only; Guide owns no invoke command.
- [ ] Run Vitest, Tauri window-scope tests, Playwright at 100/125/200 percent simulated scale, and asset-manifest validation.
- [ ] Commit with `feat(v3): add isolated fairy presence`.

### Task 39: Typed System Actions Without Host Shell

**Files:**
- Create: `core/src/fairy_core/system_actions/models.py`
- Create: `core/src/fairy_core/system_actions/application.py`
- Create: `desktop/src-tauri/crates/local-worker/src/system_actions.rs`
- Modify: `desktop/src-tauri/crates/local-worker/src/protocol.rs`
- Modify: `desktop/src-tauri/crates/local-worker/src/lib.rs`
- Modify: `core/src/fairy_core/workspace/worker_transport.py`
- Modify: `core/src/fairy_core/commanding/registry.py`
- Modify: `capabilities/src/fairy_capabilities/composition.py`
- Test: `core/tests/system_actions/test_application.py`
- Test: `desktop/src-tauri/crates/local-worker/tests/system_actions.rs`
- Test: `desktop/src-tauri/crates/local-worker/tests/module_boundaries.rs`

**Interfaces:**
- Tool names are `system.open_url`, `system.reveal_path`, `system.copy_text`, `system.notify`, and `system.open_settings`.
- Rust receives tagged enums only. The protocol has no `program`, `args`, `command`, `shell`, `script`, registry, keyboard, pointer, or arbitrary URI field.

- [ ] Write failing Core tests for CommandRun creation, profile approval, HTTPS-only URL, managed-path identity, text limits, notification limits, and fixed Settings enum.
- [ ] Write failing Rust protocol tests that reject unknown fields, non-HTTPS URLs, path escape, oversized text, unsupported Settings names, and every shell-shaped payload.
- [ ] Implement typed actions with OS library calls and exact Core-resolved inputs; keep model-provided path/Scope fields discarded.
- [ ] Add recovery/idempotency semantics so repeated action keys complete once or return the prior result.
- [ ] Extend the structural boundary test to reject process-spawn APIs from `system_actions.rs`.
- [ ] Run Core command/policy/security tests and full Rust workspace fmt/clippy/tests.
- [ ] Commit with `feat(v3): add typed host actions`.

### Task 40: Cloud Completion, Acceptance, Performance, and Cleanup

**Files:**
- Modify: `cloud/src/fairy_cloud/api.py`
- Modify: `cloud/src/fairy_cloud/dispatchers.py`
- Modify: `cloud/src/fairy_cloud/workers/outbox.py`
- Modify: `cloud/compose.yaml`
- Modify: `scripts/test-all.ps1`
- Modify: `scripts/check_boundaries.py`
- Modify: `docs/architecture.md`
- Modify: `docs/threat-model.md`
- Modify: `README.md`
- Create: `docs/adr/0006-task-bound-assistant-and-capability-adapters.md`
- Create: `docs/adr/0007-document-rag-is-not-memory-authority.md`
- Test: `cloud/tests/integration/test_assistant_recovery.py`
- Test: `cloud/tests/integration/test_two_device_assistant_sync.py`
- Test: `cloud/tests/integration/test_capability_outbox.py`
- Test: `desktop/e2e/release.spec.ts`

**Interfaces:**
- Cloud REST/SSE implements the same generated Core methods and event envelopes as local JSON-RPC.
- Docker integration validates PostgreSQL 18, S3, RLS, leases, recovery, outbox, documents, evidence, and two-device conflicts; no Redis/NATS service is introduced.

- [ ] Write failing real-PostgreSQL tests for Turn lease fencing, crash at every persisted phase, no duplicate Message/tool effect, RLS, event/outbox atomicity, SSE resume/de-duplication, offline local continuation, and two-device Version conflict.
- [ ] Implement cloud adapter composition and worker recovery until the Docker tests pass when a Docker CLI/daemon is available.
- [ ] Add contract parity tests for every new local/cloud method and public error code.
- [ ] Run the complete black-box workflow matrix: scratch, project, provider unavailable/fallback, research, information, documents/RAG, Hermes, voice, perception, Presence, approval, Preview, accept/discard, offline, conflict, and recovery.
- [ ] Measure shell interactive time, Core readiness, ledger-event-to-UI p95, and initial gzip size; fail the gate above 1.5 s, 3 s, 100 ms, or 800 KB respectively.
- [ ] Remove obsolete compatibility paths, split oversized modules, remove empty future-facing directories, scan for legacy imports, secrets, host-shell APIs, keyword routers, browser speech synthesis, and duplicate memory authorities.
- [ ] Run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-all.ps1` and preserve explicit Docker/WSL skip reporting when those runtimes are unavailable.
- [ ] Verify a single Alembic head, offline upgrade/downgrade, clean generated contracts, clean Git status, and documentation consistency.
- [ ] Commit with `chore(v3): complete assistant capability platform`.

## Self-review record

- Spec coverage maps every remaining original capability to Tasks 29 through 40.
- The plan contains no runtime dependency from Core to adapters and no second
  memory authority.
- Local and cloud contracts share exact method/type names through generation.
- Every production slice has a preceding failing test step, a package gate,
  and an independent commit.
- Docker Compose is a repository environment; an unavailable local Docker
  CLI/daemon is reported as an integration skip, not as an absent environment.
