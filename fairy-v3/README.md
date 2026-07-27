# Fairy V3

Fairy V3 is a new Windows-first desktop AI application. It is developed in
parallel with the legacy Fairy implementation and does not import legacy
runtime modules or migrate legacy databases.

The product has two first-class workspaces:

- scratch conversations for general assistant tasks;
- project conversations for scoped, versioned project execution.

The architecture is project-first, task-driven, preview-first, and
core-owned. Every side effect crosses the command bus and is recorded in the
durable ledger before execution.

## Repository layout

```text
fairy-v3/
|- core/       Python domain, application services, and transports
|- capabilities/ Replaceable model and product capability adapters
|- desktop/    React/Tauri shell and the Rust local worker
|- contracts/  Generated OpenAPI, JSON Schema, TypeScript, and Rust contracts
|- cloud/      Cloud API/worker composition and deployment files
|- config/     Secret-free provider profile presets
|- resources/  Provenanced character, icon, and voice assets
|- scripts/    Contract generation and repository boundary checks
|- docs/       Architecture, threat model, ADRs, and operating documentation
`- tools/      Isolated contract generators and developer tooling
```

Tests stay with the package that owns the behavior: `core/tests`,
`cloud/tests`, `desktop/src/**/*.test.ts(x)`, and `desktop/e2e`. This keeps
fixtures and runtime dependencies inside their actual boundary.

The implementation is intentionally independent. Legacy character assets,
voice assets, doctrine, and black-box behavior may be used as references only.
The requirement-by-requirement release record is maintained in
`docs/completion-audit.md`; architecture decisions live under `docs/adr/`.

## Release scope

The V3 application shell and durable platform are implemented. Tauri
supervises the composed Python Core, owns isolated windows and credentials, and
hosts the Rust local worker. Project import, worktrees, Changesets, approval,
review, Preview, checkpoint, accept/discard, artifacts, capabilities, and
resumable events all cross Core and the Command Bus. React renders the actual
Task Timeline, Preview, scratch chat, provider settings, voice, perception,
Workspace, Settings, and the programmatic Fairy Pet through generated contracts.

Assistant execution is Task-bound and durable. Messages, Turns, Tool
Invocations, model rounds, public deltas, cancellation, retry, and lease-aware
recovery use the same SQLite/PostgreSQL Unit of Work. `fairy-capabilities`
provides OpenAI-compatible local/cloud model adapters, explicit fallback,
research, news/weather/time/maps/market information, managed documents and
RAG, STT/TTS, and bounded perception adapters. Model tool calls remain
untrusted candidates and every effect is a registered Command. The Windows
host surface is limited to typed URL/path/clipboard/notification/settings
actions with a durable idempotency journal; there is no host shell API.

Governed Fairy Skills provide versioned, provenance-checked instruction
packages. MCP `2025-11-25` tools use explicit server trust and per-tool policy,
one Registry, Scope-bound CommandRuns, approvals, cancellation, schema drift
checks, and Task-owned Artifacts. Local composition supports configured stdio
or Streamable HTTP; Cloud supports Streamable HTTP only. Credentials and
transport configuration are never model-visible.

Hermes relational Observations, Claims, revisions, Tombstones, lexical
projection, retrieval health, and immutable Task Snapshots are canonical
memory. Managed documents are a separate revisioned local/S3 corpus; RAG and
future vector indexes cannot become memory authority or silently write Claims.

Cloud composition includes FastAPI REST/SSE parity, OIDC, PostgreSQL 18 forced
RLS, global cursor synchronization, optimistic two-device version promotion,
candidate conflict retention, S3-compatible immutable objects, transactional
Outbox, fenced Worker leases, and lease-aware Assistant/Runtime recovery. The
Outbox Worker validates complete EventEnvelopes and delivery identity. It is a
non-root brokerless projection worker, not a project execution sandbox; no
Redis, NATS, Docker socket, or host mount is introduced.

Read-only static Preview works without WSL or Docker. Dynamic project commands
remain capability-gated: `run.sandboxed` is exposed only when the composed
FairySandbox or cloud OCI executor is configured and healthy, and never falls
back to Windows process execution. Episodes, pgvector semantic expansion, and
parallel projection generations are outside the V3 canonical-memory contract;
their absence does not create a second or incomplete memory authority.

Run every locally available release gate with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-all.ps1
```

The command runs Core, Capabilities, and Cloud lint/tests, offline Alembic DDL,
Rust checks, production-build Playwright workflows, contract regeneration,
repository safety/structure checks, and release performance gates. The budgets
are 1.5 seconds to interactive shell, 3 seconds to composed Core readiness,
100ms event-to-UI p95, and 800KiB conservative renderer gzip.
When Docker is available it also runs the real PostgreSQL/S3 integration
profile; otherwise it reports those integration tests as explicitly skipped.
The default run also reports WSL verification as skipped. Require a real
FairySandbox attestation with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-all.ps1 -RequireWslSandbox
```

Use `-SkipDocker` when an intentionally local-only gate is required. Static
Preview itself does not require Docker or WSL.

Realtime Companion has two separate long-session gates. The deterministic
four-hour-equivalent state-machine scenario runs with the Rust workspace:

```powershell
cd desktop\src-tauri
cargo test --test realtime_soak
```

The real wall-clock gate is intentionally not part of ordinary development or
CI. It requires a Windows release executable, a dedicated Fairy data directory
whose managed MiniCPM install is already `ready`, explicit Local runtime
verification, and at least four hours. It never accepts Cloud fallback and
writes only sanitized counters and public status codes:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\test-realtime-companion-soak.ps1 `
  -Executable C:\FairySoak\fairy.exe `
  -DataDirectory C:\FairySoak\data `
  -ConfirmDataDirectory `
  -DurationHours 4 `
  -OutputPath C:\FairySoak\evidence\realtime-soak.json
```

The operator verifies the Local model, starts Realtime Companion, and stops it
after the duration prompt so Core can finalize the Session digest. The harness
terminates its complete process tree in all paths. Missing eligible hardware,
model/runtime readiness, a completed Session, or a terminal digest is a
blocked/failed release gate rather than a synthetic pass.

For local OpenRouter development, the checked-in preset exposes only the fixed
Composer allowlist. Cross-model fallback is chosen per Auto Turn by Core and is
never applied to a manual model selection. Start the Tauri application through
the repository launcher. It creates a temporary
ASCII path alias when Vite cannot safely serve the repository path. The key is
optional; when supplied, the script reads it only into the child-process
environment and never copies it into the repository:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-desktop.ps1 `
  -OpenRouterKeyFile C:\secure\openrouter.txt
```

Running the script without `-OpenRouterKeyFile` starts Core with the same
secret-free provider profiles and reports their credentials as unavailable.
Opening the Vite URL in a normal browser intentionally shows the desktop-host
boundary instead of a disabled workspace because local Core IPC exists only in
the Tauri application.

The secret-free allowlist is stored in `config/openrouter.providers.json`.
Model availability, pricing, and rate limits are controlled by OpenRouter, so
the live catalog and provider health remain authoritative at runtime.

Run only the dependency and layer boundary gate with:

```powershell
uv run --project core python scripts/check_boundaries.py .
```

Build the signed-ready Windows installer inputs with:

```powershell
cd desktop
npm run tauri build
```

This produces MSI and NSIS bundles containing the generated Core sidecar and
pinned MinGit runtime. Signing credentials are deployment secrets and are not
stored in the repository.
