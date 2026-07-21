# Audit Repair Finalization Acceptance

## Observable behavior

- Obsidian remains optional and device-local. Cloud transports reject Vault path
  operations before network I/O, while normalized Knowledge contracts remain usable.
- Knowledge, Memory, Skills, MCP, model selection, and Workspace revisions used by a
  Turn remain bound to its immutable Harness manifest.
- Settings shows all active project Knowledge Sources, pending Memory proposals, and
  connector diagnostics without gaining execution or file-write capabilities.
- Switching project or Source clears an open note immediately. A late read from the
  previous scope cannot replace content in the active scope.
- Realtime sessions can be cancelled during startup and active playback is interrupted
  within the existing full-duplex budget.
- Refactoring Registry, contracts, Cloud routers, Desktop settings, and Workspace model
  modules does not change their public import or RPC behavior.

## Invariants and state ownership

- Fairy Core is the sole authority for Memory settings, Knowledge revisions, snapshots,
  Harness manifests, Task state, and Ledger events.
- Device Vault paths remain in the native path registry. Domain contracts, logs, Ledger,
  model context, and Cloud requests carry only stable Source identities and safe display
  paths.
- Note UI state is keyed by `project_id + source_id + content_hash`.
- Knowledge and Graph caches are keyed by project identity and the Core graph watermark.
- Settings is read-only for active Projects, Tasks, and Sources except for dedicated
  Memory proposal decisions and Memory settings updates.
- Local-only RPC methods never appear as Cloud routes and are marked explicitly in the
  generated transport manifest.

## Risks

- Mechanical module moves can create circular imports or silently omit public exports.
- Dynamic multi-Source queries can associate a response with the wrong Source after a
  rapid project switch.
- Contract regeneration can hide drift if generated output is not compared after a
  clean generation.
- Optional Obsidian or Voice environments can be mistaken for verified integration when
  only mocked or static tests ran.

## Acceptance scenarios

1. Two projects and two Sources: filter between Sources, start a note read, switch
   project, resolve the old request, and verify the old note never renders.
2. Settings: list project Sources and a pending Memory proposal, require an explicit
   confirmation dialog, and verify only the proposal RPC is sent.
3. Window scope: verify settings can list Projects/Sources and read connector health but
   cannot execute assistant turns, mutate Projects, delete conversations, or invoke
   system actions.
4. Transport matrix: assert every public Core method has one declared availability and
   that local-only methods are absent from Cloud OpenAPI routes.
5. Refactor compatibility: import existing Registry/contracts/Cloud app/Desktop hooks
   through their current public modules and run existing contract and integration tests.
6. Realtime: cover startup cancellation, duplicate stop, timeout, worker interruption,
   and Fairy/provider voice barge-in without persisting media or transcript content.

## Required environments and evidence

- Structural: boundary checker, Ruff, Python type checking, TypeScript, generated
  contract drift, Rust format, Clippy, and module-boundary tests.
- Automated behavior: Core, Capabilities, Cloud, Desktop Vitest, Rust workspace, and
  relevant Playwright suites.
- Integration: migration upgrade/downgrade and Docker PostgreSQL contract tests once at
  final verification.
- Real environment: one local Obsidian test Vault, one live GLM Realtime smoke, and one
  Tauri multi-window/Presence regression. Missing credentials, capture support, or GUI
  access must be reported as unverified rather than passed.

## Automation boundaries

- Vitest uses mocked Core and cannot prove native window scope or real Vault file
  identity checks.
- Rust unit tests cannot prove live WGC, WebView2, DPI, or interactive focus behavior.
- Provider tests without the live environment flag do not prove external service
  availability or latency.
- Docker and PostgreSQL claims require the real containers and migrations, not SQLite
  or static schema inspection.

## 2026-07-21 Verification Record

Passed in the current worktree:

- Core: 777 tests.
- Capabilities: 118 tests.
- Cloud: 121 unit/contract tests; one PostgreSQL DSN test skipped and integration
  tests deselected.
- Desktop: TypeScript, 74 Vitest files / 357 tests, 57 Playwright functional
  scenarios, and one isolated Playwright performance scenario.
- Rust: workspace format, Clippy with warnings denied, and workspace tests.
- Python: Ruff format/lint and repository boundary checks. No Python static type
  checker is configured, so no Python type-check claim is made.
- Contracts and storage: generated OpenAPI/RPC drift checks and full Alembic
  offline upgrade/downgrade.
- Packaging surface: one Vite production build; no Tauri release or Docker image
  build was performed.

Environment gates still requiring a release-candidate run:

- Docker/PostgreSQL/S3 integration: Docker CLI is installed, but the daemon was
  unavailable during this verification.
- Real Obsidian test Vault: not run in this non-destructive automated pass.
- Live GLM Realtime: not run without an explicit live-provider smoke invocation.
- Interactive Tauri multi-screen/Presence: browser and Rust regressions passed,
  but a fresh interactive native session was not run.
