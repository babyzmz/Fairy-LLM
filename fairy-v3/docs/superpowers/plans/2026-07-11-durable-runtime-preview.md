# Durable Runtime and Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Fairy V3's sample Preview into a durable Core-owned Runtime/Preview/Artifact lifecycle, ship a safe local static Preview, and keep all dynamic execution fail-closed behind WSL2 FairySandbox health.

**Architecture:** Core owns immutable Scope and durable lifecycle state. A new RuntimeApplication persists start/stop intent around an idempotent RuntimeExecutor port; the Rust local worker implements a read-only loopback static server, while WSL health remains a separate attestation and never falls back to host execution. Local JSON-RPC and Cloud REST expose the same generated contracts, and Desktop renders only Core-resolved state and events.

**Tech Stack:** Python 3.13, Pydantic 2, SQLAlchemy 2, SQLite, PostgreSQL 18/Alembic, Rust 2021, Tauri 2, React 19.2, TanStack Query, TypeScript, Vitest, Playwright.

## Global Constraints

- Do not execute project binaries, package managers, interpreters, or shell commands on Windows.
- Bind local static Preview only to `127.0.0.1` on an OS-assigned port and serve GET/HEAD only.
- Keep `run.sandboxed`, dynamic Preview, dependency installation, and executable reviews disabled unless `FairySandbox` attestation is healthy.
- Never auto-install WSL, import/unregister a distribution, edit Hyper-V firewall, or request elevation.
- Never mount Windows drives inside FairySandbox; required `wsl.conf` settings are `automount.enabled=false`, `mountFsTab=false`, `interop.enabled=false`, and `appendWindowsPath=false`.
- Persist external intent before dispatch and persist completion/failure after idempotent executor return.
- Keep Scope IDs, paths, ports, URLs, executor handles, and capability health Core/executor-owned; model and renderer values are untrusted.
- Keep Core runtime dependencies limited to Pydantic and SQLAlchemy.
- Use one linear Alembic head, forced PostgreSQL RLS, explicit tenant predicates, and reversible upgrade/downgrade SQL.
- Run all commands from the ASCII `V:\fairy-v3` mapping on this Windows workspace.
- Commit every Task independently. Docker and WSL integration must be reported as skipped when their real runtimes are unavailable.

---

### Task 18: Define Runtime, Preview, and Artifact State Machines

**Files:**
- Modify: `fairy-v3/core/src/fairy_core/domain/execution.py`
- Modify: `fairy-v3/core/src/fairy_core/domain/errors.py`
- Modify: `fairy-v3/core/tests/test_execution_domain.py`
- Create: `fairy-v3/core/tests/test_runtime_properties.py`

**Interfaces:**
- Produces: `RuntimeKind`, `RuntimeStatus`, `RuntimeHealth`, `PreviewStatus`, `PreviewHealth`, `ArtifactType`, `RuntimeSession`, `PreviewSession`, and enriched immutable `Artifact`.
- Preserves: existing `Changeset`, `Approval`, and `Checkpoint` interfaces.

- [ ] **Step 1: Write failing state-machine tests**

Add table/property tests asserting every permitted edge from the design and rejecting all other Runtime/Preview transitions. Assert that a Runtime cannot become `running` without a non-empty executor handle and loopback port, a local Preview cannot become `ready` without an `http://127.0.0.1:<port>/...` URL, IDs/roots cannot be rebound, and Artifact hashes/lengths are validated.

```python
def test_ready_local_preview_requires_loopback_runtime() -> None:
    runtime = runtime_fixture()
    runtime.begin_start()
    runtime.mark_running(executor_handle="static:preview-1", port=43125)
    preview = preview_fixture(runtime)
    preview.begin_start()
    preview.mark_ready("http://127.0.0.1:43125/preview-1/")
    assert preview.status is PreviewStatus.READY
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `uv run --project core pytest core/tests/test_execution_domain.py core/tests/test_runtime_properties.py -q`

Expected: collection/import failures because the new enums and transition methods do not exist.

- [ ] **Step 3: Implement strict domain types**

Replace string statuses on RuntimeSession/PreviewSession with enums and explicit methods:

```python
class RuntimeSession:
    @classmethod
    def create(cls, *, scope: ScopeContract, kind: RuntimeKind, executor: str,
               idempotency_key: str) -> RuntimeSession: ...
    def begin_start(self) -> None: ...
    def mark_running(self, *, executor_handle: str, port: int) -> None: ...
    def begin_stop(self) -> None: ...
    def mark_stopped(self) -> None: ...
    def mark_failed(self, error_code: str) -> None: ...
    def mark_interrupted(self, error_code: str = "WORKER_INTERRUPTED") -> None: ...

class PreviewSession:
    @classmethod
    def create(cls, *, scope: ScopeContract, runtime_id: UUID,
               visibility: str, idempotency_key: str) -> PreviewSession: ...
    def begin_start(self) -> None: ...
    def mark_ready(self, url: str) -> None: ...
    def begin_stop(self) -> None: ...
    def mark_stopped(self) -> None: ...
    def mark_failed(self, error_code: str) -> None: ...
    def mark_interrupted(self, error_code: str = "WORKER_INTERRUPTED") -> None: ...
```

Use `InvalidTransitionError` for illegal state changes and a typed `PreviewScopeViolationError` with stable code `SCOPE_MISMATCH` for rebinding/scope failures.

- [ ] **Step 4: Verify Task 18**

Run:

```powershell
uv run --project core ruff format core/src/fairy_core/domain core/tests/test_execution_domain.py core/tests/test_runtime_properties.py
uv run --project core ruff check core/src/fairy_core/domain core/tests
uv run --project core pytest core/tests/test_execution_domain.py core/tests/test_runtime_properties.py -q
```

Expected: all domain/property tests pass.

- [ ] **Step 5: Commit Task 18**

```powershell
git add fairy-v3/core/src/fairy_core/domain fairy-v3/core/tests/test_execution_domain.py fairy-v3/core/tests/test_runtime_properties.py
git commit -m "feat(v3): define durable runtime preview domain"
```

---

### Task 19: Persist Runtime, Preview, and Artifact with Tenant Isolation

**Files:**
- Modify: `fairy-v3/core/src/fairy_core/storage/ports.py`
- Modify: `fairy-v3/core/src/fairy_core/storage/schema.py`
- Modify: `fairy-v3/core/src/fairy_core/storage/sqlalchemy.py`
- Modify: `fairy-v3/core/tests/test_state_store.py`
- Modify: `fairy-v3/core/tests/test_repository_boundaries.py`
- Create: `fairy-v3/cloud/migrations/versions/20260711_0007_runtime_preview.py`
- Modify: `fairy-v3/cloud/tests/test_deployment_contract.py`
- Modify: `fairy-v3/cloud/tests/test_postgres_contract.py`
- Modify: `fairy-v3/cloud/tests/integration/conftest.py`
- Modify: `fairy-v3/cloud/tests/integration/test_postgres_tenant_rls.py`

**Interfaces:**
- Produces StateStore methods:

```python
def append_runtime(self, runtime: RuntimeSession) -> RuntimeSession: ...
def save_runtime(self, runtime: RuntimeSession, *, expected_revision: int) -> RuntimeSession: ...
def get_runtime(self, runtime_id: UUID) -> RuntimeSession | None: ...
def find_runtime_by_idempotency_key(self, key: str) -> RuntimeSession | None: ...
def runtimes_for_task(self, task_id: UUID) -> list[RuntimeSession]: ...
def append_preview(self, preview: PreviewSession) -> PreviewSession: ...
def save_preview(self, preview: PreviewSession, *, expected_revision: int) -> PreviewSession: ...
def get_preview(self, preview_id: UUID) -> PreviewSession | None: ...
def find_preview_by_idempotency_key(self, key: str) -> PreviewSession | None: ...
def preview_for_task(self, task_id: UUID, *, include_terminal: bool = False) -> PreviewSession | None: ...
def previews_for_conversation(self, conversation_id: UUID) -> list[PreviewSession]: ...
def append_artifact(self, artifact: Artifact) -> Artifact: ...
def get_artifact(self, artifact_id: UUID) -> Artifact | None: ...
def artifacts_for_task(self, task_id: UUID) -> list[Artifact]: ...
```

- [ ] **Step 1: Add failing SQLite and migration contract tests**

Assert round-trip fidelity, same idempotency-key replay, one non-terminal Preview per Task, immutable Artifact conflict rejection, and tenant predicates. Extend offline DDL assertions for all three tables, foreign keys, checks, partial uniqueness, RLS enable/force/policy, and one head `20260711_0007`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
uv run --project core pytest core/tests/test_state_store.py core/tests/test_repository_boundaries.py -q
uv run --project cloud pytest cloud/tests/test_deployment_contract.py cloud/tests/test_postgres_contract.py -q
```

Expected: missing StateStore methods/tables and migration-head mismatch.

- [ ] **Step 3: Add schema and repository mappings**

Create `core_runtime_sessions`, `core_preview_sessions`, and `core_artifacts` in shared SQLAlchemy metadata. Store enum values, scope IDs, revisions, idempotency keys, timestamps, executor metadata, hash/length, JSON metadata, and explicit tenant keys. Use insert-once verification for Artifact; use optimistic `revision` predicates for Runtime/Preview updates so stale finalizers fail.

- [ ] **Step 4: Add reversible Alembic migration and RLS**

Revision `20260711_0007` must depend on `20260711_0006`, create tables/indexes/checks in dependency order, enable and force RLS, and create `tenant_isolation_<table>` policies using `current_setting('app.tenant_id', true)`. Downgrade removes policies, indexes, and tables in reverse order.

- [ ] **Step 5: Verify Task 19**

Run Core tests, Cloud unit tests, `uv run --project cloud alembic upgrade head --sql`, and `uv run --project cloud alembic downgrade head:base --sql`.

- [ ] **Step 6: Commit Task 19**

```powershell
git add fairy-v3/core/src/fairy_core/storage fairy-v3/core/tests fairy-v3/cloud/migrations fairy-v3/cloud/tests
git commit -m "feat(v3): persist runtime preview state"
```

---

### Task 20: Add the Rust Read-only Static Preview Executor

**Files:**
- Modify: `fairy-v3/desktop/src-tauri/crates/local-worker/Cargo.toml`
- Create: `fairy-v3/desktop/src-tauri/crates/local-worker/src/preview.rs`
- Modify: `fairy-v3/desktop/src-tauri/crates/local-worker/src/lib.rs`
- Create: `fairy-v3/desktop/src-tauri/crates/local-worker/tests/static_preview.rs`
- Modify: `fairy-v3/desktop/src-tauri/crates/local-worker/tests/stdio_protocol.rs`

**Interfaces:**
- Consumes: Fairy-managed `project_id`, `version_id`, `preview_id`, and fixed `entry_path`.
- Produces typed worker methods:

```text
preview.start_static -> {executor_handle, host, port, url}
preview.status       -> {state, host, port, url}
preview.stop         -> {stopped: true}
```

- [ ] **Step 1: Add failing Rust protocol and HTTP security tests**

Cover loopback/ephemeral binding, `index.html`, GET, HEAD, 404, 405, no directory listing, `../`, `%2e%2e`, mixed separators, invalid UTF-8 percent sequences, query/fragment stripping, symlink and Windows reparse escape, duplicate start/status/stop, and drop cleanup. Assert the worker never invokes `Command` for Preview.

- [ ] **Step 2: Run tests and verify RED**

Run: `cargo test -p fairy-local-worker --test static_preview --test stdio_protocol`

Expected: missing `preview` module and unknown worker methods.

- [ ] **Step 3: Implement a focused Preview module**

Use locked dependencies `tiny_http`, `percent-encoding`, and `mime_guess`. `StaticPreviewManager` owns a mutex-protected map of Preview ID to stop channel/thread/metadata. Canonicalize the Version root and each requested file, reject symlink/reparse components, and require the result to remain under root. Add `nosniff`, no-store HTML caching, bounded media caching, and a CSP that blocks external origins and navigation while allowing same-origin static scripts/styles.

- [ ] **Step 4: Wire typed JSON-RPC without changing workspace APIs**

Create an internal `LocalWorker` composition containing `WorkspaceManager` and `StaticPreviewManager`. Preserve existing public WorkspaceManager tests and `process_stream` behavior; keep Preview registry alive for the entire stdio stream. Map validation to `PATH_OUT_OF_SCOPE`, duplicate conflicts to `SCOPE_MISMATCH`, and lost server state to `WORKER_INTERRUPTED`.

- [ ] **Step 5: Verify Task 20**

Run:

```powershell
cargo fmt --check
cargo clippy -p fairy-local-worker --all-targets -- -D warnings
cargo test -p fairy-local-worker --all-targets
```

- [ ] **Step 6: Commit Task 20**

```powershell
git add fairy-v3/desktop/src-tauri
git commit -m "feat(v3): serve scoped static previews"
```

---

### Task 21: Define RuntimeExecutor and WSL Health Attestation

**Files:**
- Create: `fairy-v3/core/src/fairy_core/runtime/__init__.py`
- Create: `fairy-v3/core/src/fairy_core/runtime/models.py`
- Create: `fairy-v3/core/src/fairy_core/runtime/ports.py`
- Create: `fairy-v3/core/src/fairy_core/runtime/rust_worker.py`
- Create: `fairy-v3/core/src/fairy_core/runtime/wsl_health.py`
- Create: `fairy-v3/core/tests/test_runtime_executor.py`
- Create: `fairy-v3/core/tests/test_wsl_health.py`
- Modify: `fairy-v3/core/src/fairy_core/commanding/registry.py`
- Modify: `fairy-v3/core/tests/test_policy.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class RuntimeExecutorHealth:
    available: bool
    executor: str
    version: str | None
    error_code: str | None
    diagnostics: tuple[str, ...]

class RuntimeExecutor(Protocol):
    def health(self) -> RuntimeExecutorHealth: ...
    def start_static(self, request: StaticRuntimeStart) -> RuntimeStartResult: ...
    def probe(self, executor_handle: str) -> RuntimeProbeResult: ...
    def stop(self, executor_handle: str) -> RuntimeStopResult: ...
```

`models.py` also defines validated `StaticRuntimeStart`, `RuntimeStartResult`,
`RuntimeProbeResult`, and `RuntimeStopResult` dataclasses. Application request
types remain in Task 22 so executor and public transport inputs do not couple.

- [ ] **Step 1: Write failing adapter/attestation tests**

Use fake WorkerTransport and fake subprocess runner. Cover result shape, loopback URL validation, malformed handle/port, worker interruption, missing `wsl.exe`, missing distro, WSL1, root default user, wrong runner version, missing config keys, and a fully attested config. Assert no invocation uses `shell=True` and no health check changes system state.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run --project core pytest core/tests/test_runtime_executor.py core/tests/test_wsl_health.py core/tests/test_policy.py -q`

- [ ] **Step 3: Implement adapters**

`RustRuntimeExecutor` maps fixed methods to WorkerTransport. `WslSandboxHealthProbe` executes only fixed `wsl.exe --status`, `--list --verbose`, and `--distribution FairySandbox --user fairy --exec ...` probes with hidden windows, timeouts, strict UTF-16/UTF-8 decoding, and no inherited secrets. It returns `SANDBOX_UNAVAILABLE` for every incomplete attestation.

- [ ] **Step 4: Keep capability health independent**

Continue passing one `sandbox_healthy` boolean into the Tool Registry. Static Preview health must not affect it. Add tests that `preview.start` is available in Standard with policy approval while `run.sandboxed` remains false when WSL is absent.

- [ ] **Step 5: Verify and commit Task 21**

Run full Core Ruff/tests, then commit:

```powershell
git add fairy-v3/core/src/fairy_core/runtime fairy-v3/core/src/fairy_core/commanding fairy-v3/core/tests
git commit -m "feat(v3): add runtime executor boundaries"
```

---

### Task 22: Orchestrate Durable Preview Start, Stop, Resolve, and Recovery

**Files:**
- Create: `fairy-v3/core/src/fairy_core/application/runtime.py`
- Modify: `fairy-v3/core/src/fairy_core/application/core.py`
- Modify: `fairy-v3/core/src/fairy_core/persistence/unit_of_work.py`
- Create: `fairy-v3/core/tests/test_runtime_application.py`
- Create: `fairy-v3/core/tests/test_runtime_recovery.py`
- Modify: `fairy-v3/core/tests/test_local_project_loop.py`

**Interfaces:**

```python
class RuntimeApplication:
    def start_preview(self, request: PreviewStartRequest) -> PreviewContext: ...
    def stop_preview(self, request: PreviewStopRequest) -> PreviewSession: ...
    def get_preview(self, preview_id: UUID) -> PreviewContext: ...
    def resolve_preview(self, request: PreviewResolveRequest) -> PreviewContext | None: ...
    def runtime_health(self, task_id: UUID) -> RuntimeHealthResult: ...
    def recover_interrupted(self) -> tuple[PreviewSession, ...]: ...
```

Define immutable application request/context dataclasses in this module:

```python
@dataclass(frozen=True, slots=True)
class PreviewStartRequest:
    task_id: UUID
    idempotency_key: str

@dataclass(frozen=True, slots=True)
class PreviewStopRequest:
    preview_id: UUID
    idempotency_key: str

@dataclass(frozen=True, slots=True)
class PreviewResolveRequest:
    conversation_id: UUID
    preview_id: UUID | None = None
```

- [ ] **Step 1: Write failing lifecycle/recovery tests**

Test the complete static flow after Changeset approval: Task `executing -> previewing -> reviewing -> ready`; same-key replay; different-payload conflict; scope/root mismatch; start failure; crash after intent; crash after external start; probe recovery; stop replay; discard blocked until stop; accept promotes ready Preview pointer; explicit resolver order; and no Project-wide newest fallback.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run --project core pytest core/tests/test_runtime_application.py core/tests/test_runtime_recovery.py core/tests/test_local_project_loop.py -q`

- [ ] **Step 3: Implement split-transaction orchestration**

Use `preview.start`/`preview.stop` ToolDefinitions and CommandBus runs. First transaction validates Core Scope, detects regular root `index.html`, creates/reuses entities, transitions to starting, appends visible event, and commits. Dispatch through RuntimeExecutor outside the transaction. Final transaction checks entity revision and executor result, transitions Runtime running/Preview ready, updates Task/Conversation, completes the command, and commits. Failure finalizer records typed error and never fabricates ready state.

- [ ] **Step 4: Integrate review, accept, and discard invariants**

Allow `CoreApplication.review_task` from `PREVIEWING`. On accept, persist the ready Preview as Project active Preview with the accepted Version. On discard, reject while a non-terminal Preview exists; the public facade must stop first. Do not stop a promoted Preview during accept.

- [ ] **Step 5: Implement recovery**

For starting/running/stopping Runtime rows, probe the executor handle if present. Complete ready state only from a matching running probe. Mark unknown/absent processes interrupted. Retry uses the same Runtime/Preview IDs and a revision fence; concurrent recovery cannot dispatch twice.

- [ ] **Step 6: Verify and commit Task 22**

Run full Core tests and commit:

```powershell
git add fairy-v3/core/src/fairy_core/application fairy-v3/core/src/fairy_core/persistence fairy-v3/core/tests
git commit -m "feat(v3): orchestrate durable previews"
```

---

### Task 23: Complete Collection Query Contracts Needed by Desktop

**Files:**
- Modify: `fairy-v3/core/src/fairy_core/storage/ports.py`
- Modify: `fairy-v3/core/src/fairy_core/storage/sqlalchemy.py`
- Modify: `fairy-v3/core/src/fairy_core/contracts/models.py`
- Modify: `fairy-v3/core/src/fairy_core/contracts/methods.py`
- Modify: `fairy-v3/core/src/fairy_core/application/service.py`
- Modify: `fairy-v3/core/tests/test_core_service.py`
- Modify: `fairy-v3/core/tests/test_contracts.py`

**Interfaces:**
- Produces tenant-scoped, deterministically ordered methods:
  `projects.list`, `conversations.get/list`, `tasks.list`, `versions.list`, and
  `approvals.list`.
- Every list uses bounded `limit` (1-100), opaque cursor or stable
  `(created_at, id)` ordering, and optional owning Scope filter.

- [ ] **Step 1: Write failing query/tenant tests**

Seed multiple projects, conversations, versions, tasks, and approvals with
same IDs in two tenants. Assert ownership filters, stable order, empty pages,
limit validation, and no unscoped cross-tenant result.

- [ ] **Step 2: Verify RED, implement repository queries, and map contracts**

Keep SQL in StateStore adapters. CoreService validates request models and maps
domain rows; it does not query SQL directly. Return typed page models with
`items` and `next_cursor`.

- [ ] **Step 3: Verify and commit Task 23**

Run Core Ruff/full tests and commit:

```powershell
git add fairy-v3/core/src/fairy_core/storage fairy-v3/core/src/fairy_core/contracts fairy-v3/core/src/fairy_core/application fairy-v3/core/tests
git commit -m "feat(v3): expose workspace collection queries"
```

---

### Task 24: Expose Runtime, Preview, and Artifact Across Both Transports

**Files:**
- Modify: `fairy-v3/core/src/fairy_core/contracts/models.py`
- Modify: `fairy-v3/core/src/fairy_core/contracts/methods.py`
- Modify: `fairy-v3/core/src/fairy_core/application/service.py`
- Modify: `fairy-v3/core/tests/test_contracts.py`
- Modify: `fairy-v3/core/tests/test_jsonrpc_transport.py`
- Modify: `fairy-v3/cloud/src/fairy_cloud/api.py`
- Modify: `fairy-v3/cloud/src/fairy_cloud/openapi.py`
- Modify: `fairy-v3/cloud/tests/test_http_contract.py`
- Modify: `fairy-v3/contracts/openapi.json`
- Modify: `fairy-v3/desktop/src/core/generated/api.d.ts`
- Modify: `fairy-v3/desktop/src/core/contracts.ts`
- Modify: `fairy-v3/desktop/src/core/client.ts`
- Modify: `fairy-v3/desktop/src/core/client.test.ts`

**Interfaces:**
- Adds methods from the design: `runtimes.get`, `runtimes.health`,
  `previews.start/get/resolve/stop`, `artifacts.list/read`, plus Task 23 lists.

- [ ] **Step 1: Add failing same-contract transport tests**

Invoke every method through CoreService/JSON-RPC and FastAPI. Assert request and
response validation, task-scoped authorization, stable error mapping,
idempotency header/query parity, and OpenAPI operation IDs.

- [ ] **Step 2: Implement CoreService dispatch and REST routes**

REST handlers authenticate tenant/device, obtain the tenant RuntimeApplication
from composition, and call CoreService only. No route imports StateStore or
executor adapters. Local JSON-RPC uses the same CORE_METHODS models.

- [ ] **Step 3: Regenerate and wire CoreClient**

Run `scripts/generate-contracts.ps1`, add Zod validation for Preview URLs and
enum states, and expose typed `projects`, `conversations`, `tasks`, `versions`,
`approvals`, `runtimes`, `previews`, and `artifacts` groups.

- [ ] **Step 4: Verify and commit Task 24**

Run Core/Cloud/Desktop contract tests, generated diff check, and commit:

```powershell
git add fairy-v3/core fairy-v3/cloud fairy-v3/contracts fairy-v3/desktop/src/core
git commit -m "feat(v3): expose durable preview contracts"
```

---

### Task 25: Replace Desktop Sample State with Durable Workspace and Preview

**Files:**
- Modify: `fairy-v3/desktop/src/app/App.tsx`
- Modify: `fairy-v3/desktop/src/app/WorkspaceShell.tsx`
- Modify: `fairy-v3/desktop/src/app/workspace.css`
- Create: `fairy-v3/desktop/src/app/workspaceModel.ts`
- Create: `fairy-v3/desktop/src/app/TaskTimeline.tsx`
- Create: `fairy-v3/desktop/src/app/PreviewPanel.tsx`
- Modify: `fairy-v3/desktop/src/app/App.test.tsx`
- Modify: `fairy-v3/desktop/src/app/WorkspaceShell.test.tsx`
- Create: `fairy-v3/desktop/src/app/PreviewPanel.test.tsx`
- Modify: `fairy-v3/desktop/e2e/workspace-layout.spec.ts`

**Interfaces:**
- Consumes only CoreClient public methods/events.
- Produces explicit empty/loading/offline/approval/executing/preview-ready/
  interrupted/failed/stopped/conflict UI states.

- [ ] **Step 1: Write failing component tests for durable states**

Use a typed fake CoreClient, not module mocks. Assert project/conversation/task
selection, Context Bar values, user-visible Event Timeline, approval actions,
Preview start/stop, loopback iframe source, accept/discard enablement, offline
state, and reduced motion. Assert no static sample messages, file names, prices,
ports, `路`, or mojibake remain.

- [ ] **Step 2: Implement workspace query model**

Use TanStack Query for lists/details and an abortable `events.subscribe`
consumer for invalidation. Keep selected IDs in component state and persist
only user selection, never project truth. Empty repository renders the actual
create/import workspace action area, not a marketing page.

- [ ] **Step 3: Implement Timeline and Preview panels**

Timeline renders durable `visibility=user` events only. PreviewPanel embeds
only validated Core Preview URLs, applies an iframe sandbox, and shows
loading/unavailable/interrupted/failed/stopped states. Developer drawer loads
diagnostics on demand. All icon buttons use Lucide and tooltips; layouts remain
stable at 880x680 and responsive below desktop width without text overlap.

- [ ] **Step 4: Verify Desktop**

Run:

```powershell
npm test -- --run
npm run build
npx playwright test
```

Expected: component tests, production build, and desktop layout/interaction
flows pass; gzip remains under 800 KB.

- [ ] **Step 5: Commit Task 25**

```powershell
git add fairy-v3/desktop
git commit -m "feat(v3): render durable task previews"
```

---

### Task 26: Gate Runtime Security, Recovery, PostgreSQL, and WSL Availability

**Files:**
- Create: `fairy-v3/core/tests/test_runtime_security.py`
- Extend: `fairy-v3/core/tests/test_runtime_recovery.py`
- Create: `fairy-v3/cloud/tests/integration/test_postgres_runtime_preview.py`
- Extend: `fairy-v3/cloud/tests/integration/test_postgres_tenant_rls.py`
- Create: `fairy-v3/desktop/src-tauri/crates/local-worker/tests/preview_recovery.rs`
- Modify: `fairy-v3/scripts/test-all.ps1`
- Modify: `fairy-v3/docs/threat-model.md`

**Interfaces:**
- Verifies all Task 18-25 behavior; does not add a second production API.

- [ ] **Step 1: Add adversarial path/network tests**

Cover Windows device/UNC/ADS/case paths, junction/symlink swaps, encoded
traversal, TOCTOU replacement, non-loopback URLs, forged handles/ports/Scope,
unsupported HTTP methods, oversized requests, and cross-tenant same IDs.

- [ ] **Step 2: Add crash/concurrency tests**

Crash at every start/stop transaction boundary, race duplicate starts and
recovery, kill the worker while ready, and prove one server/entity survives or
the state is explicitly interrupted. Discard cannot remove a running Version.

- [ ] **Step 3: Add real PostgreSQL/RLS integration**

Use Docker fixtures for migrations, optimistic revisions, partial Preview
uniqueness, same-ID isolation, command/outbox atomicity, and recovery. Do not
substitute compiled SQL for execution.

- [ ] **Step 4: Extend environment gates**

`test-all.ps1` continues Core/Cloud/Rust/Desktop/contracts/migrations. Docker
branch runs runtime integration. Add an opt-in `-RequireWslSandbox` switch:
default prints an explicit WSL skip; when set, missing/unattested FairySandbox
fails and a real static probe/start/stop must pass. Never say WSL passed from a
mock test.

- [ ] **Step 5: Verify and commit Task 26**

Run `scripts/test-all.ps1 -SkipDocker` on this machine, record Docker/WSL skips,
and run real gates only when available. Commit:

```powershell
git add fairy-v3/core/tests fairy-v3/cloud/tests fairy-v3/desktop/src-tauri fairy-v3/scripts fairy-v3/docs/threat-model.md
git commit -m "test(v3): gate durable preview execution"
```

---

### Task 27: Close the Durable Runtime and Preview Milestone

**Files:**
- Modify: `fairy-v3/README.md`
- Modify: `fairy-v3/core/README.md`
- Modify: `fairy-v3/cloud/README.md`
- Modify: `fairy-v3/desktop/README.md`
- Modify: `fairy-v3/docs/architecture.md`
- Create: `fairy-v3/docs/adr/0005-static-preview-and-sandbox-boundary.md`
- Modify: package `__init__.py` files only for required public exports

**Interfaces:**
- Documents delivered Runtime/Preview/Artifact lifecycle, static Preview
  threat boundary, recovery, WSL attestation, and explicit dynamic-runtime
  deferrals.

- [ ] **Step 1: Audit boundary and naming drift**

Search for string statuses, duplicate Preview/Runtime types, transport SQL,
host process invocation, shell flags, Project-wide newest Preview resolution,
unvalidated URLs, stale sample UI, compatibility exports, broad package
re-exports, migration heads, and files mixing server, protocol, persistence,
and UI responsibilities.

- [ ] **Step 2: Apply milestone-local cleanup and ADR**

Keep public contract names stable, split only mixed-responsibility files, and
record why static serving is permitted on host while project execution is not.
State exactly which Docker and WSL gates ran.

- [ ] **Step 3: Run the final clean-worktree gate**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-all.ps1 -SkipDocker
git diff --check
git status --short
```

If Docker/WSL become available, rerun without skips. Generated contracts must
be clean and the migration head must be singular.

- [ ] **Step 4: Commit Task 27**

```powershell
git add fairy-v3
git commit -m "chore(v3): close durable preview milestone"
```

## Self-review Record

- **Spec coverage:** Domain/persistence (18-19), safe local executor (20), ports/WSL health (21), orchestration/recovery (22), missing collection APIs (23), transport/client parity (24), durable Desktop state (25), security/environment gates (26), and cleanup/ADR (27) cover every design section.
- **Scope control:** Dynamic package execution, generic sandbox commands, cloud OCI execution, screenshots, and automatic WSL setup remain outside this milestone and stay unavailable rather than receiving a host fallback.
- **Type consistency:** Runtime/Preview/Artifact enums and IDs originate in Task 18, StateStore signatures in Task 19, RuntimeExecutor results in Task 21, RuntimeApplication in Task 22, and the same Pydantic/OpenAPI/TypeScript names flow through Tasks 24-25.
- **Recovery:** Every external side effect has intent-before-dispatch, idempotent executor identity, optimistic entity revision, probe-based recovery, and explicit interrupted state.
- **Placeholder scan:** No TBD/TODO or unspecified implementation/error/test step remains.
