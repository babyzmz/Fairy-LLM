# Fairy V3 Completion Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the requirements that the first completion pass incorrectly deferred or represented only with metadata, then prove the original Fairy V3 architecture end to end.

**Architecture:** Core remains the sole policy, Scope, Command, approval, and durable-state authority. Local execution uses a dedicated attested WSL2 FairySandbox through a structured protocol, while Cloud uses a separate non-root OCI execution worker backed by PostgreSQL leases; neither path exposes a Windows host shell or reuses the Outbox worker. Project tools consume persisted Workspaces and Project Indexes and every effect remains linked to a fenced CommandRun.

**Tech Stack:** Python 3.13, Pydantic 2, SQLAlchemy 2, FastAPI 0.139, PostgreSQL 18, SQLite, Rust, WSL2, OCI/Docker Compose, React 19.2, Tauri 2, Vitest, Playwright, pytest/Hypothesis.

## Global Constraints

- Execute inline because the user explicitly prohibited subagents.
- Keep Project-first, Task-driven, Preview-first behavior and Core-injected immutable Scope.
- Device-local and Cloud permission settings are separate durable authorities; renderer/model fields never select effective policy or sandbox health.
- Every side effect starts from a persisted CommandRun and uses its lease owner/fence through completion.
- `run.sandboxed` is visible only for autonomous policy with a healthy matching executor.
- No model-controlled command, shell, process, path, environment, network destination, runtime endpoint, or Scope field reaches the Windows host.
- WSL2 FairySandbox has Windows interop and host-drive automount disabled; Cloud execution is a separate non-root OCI service with no Docker socket.
- The Outbox worker remains projection-only and never receives project storage.
- Active Version promotion always requires explicit user confirmation.
- Core retains only Pydantic and SQLAlchemy runtime dependencies.
- Write tests first, observe the expected failure, implement, run focused and package gates, then commit each Task independently.
- Docker/PostgreSQL and WSL claims require actual runtime execution; unavailable environments are reported as skipped, never passed.

---

### Task 41: Make Execution Policy and Capability Health Core-owned

**Files:**
- Create: `core/src/fairy_core/commanding/settings.py`
- Create: `core/src/fairy_core/commanding/settings_sqlalchemy.py`
- Create: `core/tests/test_execution_settings.py`
- Modify: `core/src/fairy_core/storage/schema.py`
- Modify: `core/src/fairy_core/persistence/unit_of_work.py`
- Modify: `core/src/fairy_core/contracts/models.py`
- Modify: `core/src/fairy_core/contracts/methods.py`
- Modify: `core/src/fairy_core/application/service.py`
- Modify: `core/src/fairy_core/assistant/application.py`
- Modify: `core/src/fairy_core/assistant/tools.py`
- Modify: `core/src/fairy_core/system_actions/models.py`
- Modify: `core/src/fairy_core/system_actions/application.py`
- Create: `cloud/migrations/versions/20260711_0011_execution_policy.py`
- Modify: `cloud/src/fairy_cloud/api.py`
- Modify: `cloud/src/fairy_cloud/dispatchers.py`
- Modify: `desktop/src/core/client.ts`
- Modify: `desktop/src/core/cloudTransport.ts`
- Modify: `desktop/src/app/workspaceModel.ts`
- Modify: generated contracts through `scripts/generate-contracts.ps1`

**Interfaces:**
- Produces `ExecutionSettings(profile, capability_overrides, revision, updated_at)` and `ExecutionSettingsRepository.get/update(expected_revision, idempotency_key)`.
- Produces `SandboxHealthProvider.is_healthy(execution_target)`; the provider is injected by local or Cloud composition.
- Produces `permissions.get` and `permissions.update`; `capabilities.get` accepts no client policy or health fields.
- Produces `EffectiveExecutionPolicy` for Command Bus submission and model tool filtering.

- [x] **Step 1: Write failing storage and contract tests**

```python
def test_settings_update_is_revision_fenced(service):
    original = service.call("permissions.get", {})
    changed = service.call(
        "permissions.update",
        {"profile": "autonomous", "capability_overrides": {"run.sandboxed": True},
         "expected_revision": original["revision"],
         "idempotency_key": "permissions:0:autonomous"},
    )
    assert changed["revision"] == original["revision"] + 1
```

Also assert tenant isolation, idempotent replay, stale revision conflict, unknown override rejection, local/Cloud separation, and that public capability requests reject `profile` and `sandbox_healthy`.

- [x] **Step 2: Run the focused tests and observe failures caused by missing settings contracts/repository**

Run: `uv run --project core pytest tests/test_execution_settings.py tests/assistant/test_application.py tests/system_actions/test_application.py -q`

- [x] **Step 3: Implement durable settings and Core-owned effective policy**

```python
@dataclass(frozen=True, slots=True)
class EffectiveExecutionPolicy:
    profile: PermissionProfile
    capability_overrides: Mapping[str, bool]
    sandbox_healthy: bool
```

Resolve this object inside Core immediately before model tool exposure and every Command Bus submission. Remove renderer/model authority over these values.

- [x] **Step 4: Add migration, regenerate contracts, and update CoreClient**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/generate-contracts.ps1`

- [x] **Step 5: Run Task 41 verification and commit**

Run Core/Cloud unit tests, offline Alembic upgrade/downgrade, Desktop contract tests, Ruff, and boundary checks.

Verification on 2026-07-11: `scripts/test-all.ps1` exited 0 with Core 437, Capabilities 101, Cloud 60, Vitest 66, and Playwright 17 tests passing. Offline Alembic upgrade/downgrade SQL, Rust fmt/clippy/tests, production build, performance budgets, generated-contract drift, Ruff, and boundary checks passed. Real WSL and Docker/PostgreSQL/S3 gates were unavailable and explicitly skipped.

Commit: `feat(v3): make execution policy core owned`

---

### Task 42: Generalize Approval and Resume Assistant Tool Execution

**Files:**
- Modify: `core/src/fairy_core/domain/execution.py`
- Modify: `core/src/fairy_core/storage/schema.py`
- Modify: `core/src/fairy_core/storage/ports.py`
- Modify: `core/src/fairy_core/storage/sqlalchemy.py`
- Modify: `core/src/fairy_core/assistant/application.py`
- Modify: `core/src/fairy_core/assistant/repository.py`
- Modify: `core/src/fairy_core/application/core.py`
- Modify: `core/src/fairy_core/application/service.py`
- Create: `core/tests/assistant/test_approval_resume.py`
- Create: `cloud/migrations/versions/20260711_0012_generic_approval.py`
- Modify: `desktop/src/chat/useAssistantTurn.ts`
- Modify: `desktop/src/chat/ChatWorkspace.tsx`
- Modify: `desktop/e2e/chat-workspace.spec.ts`

**Interfaces:**
- Approval links exactly one `command_run_id` and may additionally link a Changeset or ToolInvocation.
- `ApprovalApplication.decide()` transitions the linked CommandRun using compare-and-swap and never executes an effect itself.
- `AssistantApplication.run()` resumes an approved queued ToolInvocation exactly once and represents rejection as a bounded tool result before continuing the Turn.

- [x] **Step 1: Write failing approval and crash-recovery tests**

Cover standard-profile approval creation, list visibility, approve/reject, duplicate decisions, crash after decision/before execution, expired lease reclaim, no duplicate tool effect, and resuming a Turn from a second Core instance.

- [x] **Step 2: Run focused tests and verify the current Changeset-only approval path fails**

Run: `uv run --project core pytest tests/assistant/test_approval_resume.py -q`

- [x] **Step 3: Implement generic approval persistence and resumable execution**

Keep provider streams and tool execution outside database transactions. The approved CommandRun must be started with its current fence before invoking the adapter.

- [x] **Step 4: Wire the Desktop approval card and event-driven Turn resume**

The renderer submits only approval ID and decision. It reloads the durable Turn/Message state after the Core event and never fabricates completion.

- [x] **Step 5: Verify and commit**

Verification on 2026-07-11: `scripts/test-all.ps1 -SkipDocker` exited 0 with Core 441, Capabilities 103, Cloud 61, Vitest 68, and Playwright 18 tests passing. Ruff, repository boundaries, offline Alembic upgrade/downgrade, Rust fmt/clippy/tests, production build, generated-contract drift, and performance gates passed; Core readiness was 836.1 ms and the initial renderer was 131.1 KiB gzip. Real WSL and Docker/PostgreSQL/S3 gates were unavailable and explicitly skipped.

Commit: `feat(v3): resume approved assistant tools`

---

### Task 43: Persist Task Workspaces and Add Project Execution Tools

**Files:**
- Create: `core/src/fairy_core/workspace/models.py`
- Create: `core/src/fairy_core/workspace/index.py`
- Create: `core/src/fairy_core/workspace/repository.py`
- Create: `core/src/fairy_core/workspace/tools.py`
- Modify: `core/src/fairy_core/workspace/ports.py`
- Modify: `core/src/fairy_core/storage/schema.py`
- Modify: `core/src/fairy_core/persistence/unit_of_work.py`
- Modify: `core/src/fairy_core/application/core.py`
- Modify: `core/src/fairy_core/application/service.py`
- Modify: `core/src/fairy_core/commanding/registry.py`
- Create: `core/tests/workspace/test_workspace_index.py`
- Create: `core/tests/workspace/test_project_tools.py`
- Create: `cloud/migrations/versions/20260711_0013_workspace_index.py`

**Interfaces:**
- `WorkspaceRepository.bind_once(task_id, version_id, root, editable_files, reference_files, constraints)` persists the Task context and rejects rebinding.
- `ProjectIndexRepository.replace_generation()` stores a deterministic file inventory, manifest/config summaries, imports, exports, symbols, and source hash per Version.
- `ProjectToolExecutor` handles `project.read`, `artifact.list`, `artifact.read`, `edit.propose_changeset`, and `preview.status` with strict generated schemas and exact Scope ownership.

- [x] **Step 1: Write failing Workspace/index and adversarial read tests**

Cover cross-tenant/cross-Version reads, junction/symlink replacement, binary/oversized files, ignored secrets, stale index generation, deterministic incremental refresh, and Task binding rollback.

- [x] **Step 2: Run tests and verify missing repository/tool adapter failures**

- [x] **Step 3: Persist Workspace and Project Index during Task creation and after Changeset apply**

Use structured parsers for JSON/TOML/Python and a bounded lexer for supported TypeScript/Rust declarations. Unknown formats remain inventory-only rather than receiving guessed semantics.

- [x] **Step 4: Implement project/artifact/Changeset tools through the existing CommandRun**

`edit.propose_changeset` creates the governed Changeset and approval but does not apply files. Tool payload Scope fields are discarded.

- [x] **Step 5: Verify migration, Core tools, local project loop, and commit**

Verification on 2026-07-11: `scripts/test-all.ps1 -SkipDocker` exited 0 with Core 452, Capabilities 103, Cloud 62, Vitest 68, and Playwright 18 tests passing. Deterministic Workspace/Index, scoped project tools, adversarial path reads, Changeset reindexing, Ruff, boundaries, offline Alembic upgrade/downgrade, Rust fmt/clippy/tests, build, contracts, and performance gates passed; Core readiness was 800.4 ms and initial renderer gzip was 131.1 KiB. Real PostgreSQL RLS and WSL gates were unavailable and explicitly skipped.

Commit: `feat(v3): add scoped project execution tools`

---

### Task 44: Implement Structured Sandbox Protocol and WSL2 FairySandbox

**Files:**
- Create: `core/src/fairy_core/sandbox/models.py`
- Create: `core/src/fairy_core/sandbox/ports.py`
- Create: `core/src/fairy_core/sandbox/wsl.py`
- Create: `sandbox/runner/fairy_sandbox_runner.py`
- Create: `sandbox/wsl/etc/wsl.conf`
- Create: `sandbox/wsl/install.ps1`
- Create: `sandbox/README.md`
- Modify: `core/src/fairy_core/runtime/wsl_health.py`
- Modify: `core/src/fairy_core/transports/stdio.py`
- Modify: `capabilities/src/fairy_capabilities/composition.py`
- Create: `core/tests/sandbox/test_models.py`
- Create: `core/tests/sandbox/test_wsl_executor.py`
- Create: `sandbox/tests/test_runner.py`
- Modify: `scripts/test-all.ps1`

**Interfaces:**
- `SandboxRequest` contains Core-injected Task/Version/Scope digest, immutable workspace generation, structured argv, bounded environment, timeout, output limit, and network policy.
- `SandboxExecutor.health/execute/cancel` returns attested executor identity, job ID, exit status, bounded stdout/stderr hashes, and timing.
- The WSL adapter invokes only `wsl.exe --distribution FairySandbox --user fairy --exec /usr/local/bin/fairy-sandbox-runner`; it never uses `shell=True` or a mounted Windows path.

- [x] **Step 1: Write failing protocol, path, environment, resource, and cancellation tests**

Cover malformed argv, NULs, overlong values, forbidden host environment keys, output floods, timeout, process tree termination, workspace generation mismatch, archive traversal, symlink escape, and attestation mismatch.

- [x] **Step 2: Run tests and observe missing sandbox interfaces**

- [x] **Step 3: Implement the runner and streamed managed-workspace synchronization**

The installer creates a non-root `fairy` user, disables automount/interoperability, installs the runner and isolation dependencies, and emits a digest-bound, root-owned health document. Workspace content crosses stdin as a bounded validated archive and is stored only in the distro ext4 filesystem.

- [x] **Step 4: Compose local capability health and fail closed when attestation fails**

Expose `run.sandboxed`, dependency, review, and dynamic Runtime commands only after a current attestation from the matching executor.

- [x] **Step 5: Verify simulated adapters and run the real WSL gate when available; commit**

Verification on 2026-07-11: `scripts/test-all.ps1 -SkipDocker` exited 0 with Sandbox Runner 25 passed/1 Windows-only POSIX process-group check skipped, Core 495, Capabilities 104, Cloud 62, Vitest 68, and Playwright 18 tests passing. Ruff, repository boundaries, PowerShell installer parsing, offline Alembic upgrade/downgrade, Rust fmt/clippy/tests, desktop build, generated contracts, and performance gates passed; Core readiness was 817.7 ms and initial renderer gzip was 131.1 KiB. The real `FairySandbox` gate was unavailable (`wsl --status failed`) and Docker/PostgreSQL/S3 integration was explicitly skipped because the Docker CLI is not installed; neither was reported as passed.

Commit: `feat(v3): add attested wsl sandbox execution`

---

### Task 45: Add the Brokerless Cloud OCI Execution Worker

**Files:**
- Create: `cloud/src/fairy_cloud/execution/models.py`
- Create: `cloud/src/fairy_cloud/execution/repository.py`
- Create: `cloud/src/fairy_cloud/execution/executor.py`
- Create: `cloud/src/fairy_cloud/workers/execution.py`
- Create: `cloud/tests/integration/test_cloud_execution.py`
- Create: `cloud/tests/test_execution_worker.py`
- Create: `cloud/migrations/versions/20260711_0014_execution_jobs.py`
- Modify: `cloud/src/fairy_cloud/dispatchers.py`
- Modify: `cloud/compose.yaml`
- Modify: `cloud/Dockerfile`
- Modify: `cloud/tests/test_deployment_contract.py`

**Interfaces:**
- PostgreSQL `execution_jobs` is tenant-scoped, idempotent, lease/fence protected, and linked to one CommandRun and Scope digest.
- `CloudSandboxExecutor.execute()` enqueues then waits/reconciles a job without re-executing an uncertain completed effect.
- `fairy_cloud.workers.execution` claims with `FOR UPDATE SKIP LOCKED`, runs one structured job as non-root, records hashes/bounded output, and acknowledges only its current fence.

- [x] **Step 1: Write failing unit and real PostgreSQL tests**

Cover enqueue atomicity, RLS, duplicate key/fingerprint conflict, dual-worker claim, stale fence, crash before spawn/after spawn/after result, cancellation, scope mismatch, and no Outbox-worker project access.

- [x] **Step 2: Implement migration/repository and run unit tests**

- [x] **Step 3: Implement non-root execution worker and Cloud composition**

The execution service receives a bounded managed-workspace archive through PostgreSQL instead of a host/project volume. It has a read-only base filesystem, private tmpfs, dropped capabilities, no Docker socket, resource limits, no raw command egress, and a distinct least-privilege database role. The Outbox worker behavior remains unchanged and its service is mount-free and no longer inherits S3 or provider credentials.

- [x] **Step 4: Run Docker PostgreSQL/S3/OCI integration when available**

Run: `docker compose -f cloud/compose.yaml --profile test up --build --abort-on-container-exit --exit-code-from integration integration`

- [ ] **Step 5: Commit**

Commit is pending because the current desktop sandbox denies writes to
`.git/worktrees/fairy-v3`; source and verification artifacts remain intact.

Verification on 2026-07-11: `scripts/test-all.ps1 -SkipDocker` exited 0 with Sandbox Runner 25 passed/1 Windows-only POSIX process-group check skipped, Core 495, Capabilities 104, Cloud 81 unit tests/22 integration tests deselected, Vitest 68, and Playwright 18 tests passing. Ruff format/check, repository boundaries, offline Alembic upgrade and full downgrade, Rust fmt/clippy/tests, desktop build, generated-contract drift, and performance gates passed; Core readiness was 810.4 ms and initial renderer gzip was 131.1 KiB. The new real PostgreSQL/OCI tests cover app-role enqueue, execution-role isolation, an attested fixed Runner, idempotent replay, and cross-tenant RLS, but were not executed because `docker`, Podman, and Docker Desktop are not installed; PostgreSQL/S3/OCI is environment-blocked and is not reported as passed.

Commit: `feat(v3): add cloud oci execution worker`

---

### Task 46: Complete Dependency, Review, and Static Preview Orchestration

**Files:**
- Create: `core/src/fairy_core/execution/templates.py`
- Create: `core/src/fairy_core/execution/application.py`
- Create: `core/src/fairy_core/application/review_evidence.py`
- Create: `core/src/fairy_core/runtime/artifacts.py`
- Create: `core/tests/execution/test_templates.py`
- Create: `core/tests/execution/test_project_closure.py`
- Modify: `core/src/fairy_core/commanding/registry.py`
- Modify: `core/src/fairy_core/application/runtime.py`
- Modify: `core/src/fairy_core/application/core.py`
- Modify: `core/src/fairy_core/application/service.py`
- Modify: `cloud/src/fairy_cloud/execution/*`
- Modify: `cloud/migrations/versions/20260711_0015_execution_purpose.py`
- Modify: `sandbox/runner/fairy_sandbox_runner.py`
- Modify: `desktop/src/app/PreviewPanel.tsx`
- Modify: `desktop/src/app/workspaceModel.ts`
- Modify: `desktop/e2e/workspace-layout.spec.ts`

**Interfaces:**
- `ProjectExecutionApplication` selects Core-owned dependency and Review templates from detected manifests; it never accepts a shell string.
- Supported dependency templates are npm, pnpm, yarn, uv, pip, and Cargo with exact cwd and lockfile policy.
- Review templates implement typecheck, lint, test, and build and persist structured results as generation-bound Artifacts.
- Dependency network access is bound to the Core-owned `dependency` Sandbox purpose in the request, standalone Runner, cloud job row, worker, and database constraints.
- Static Preview persists a canonical manifest Artifact, is resolved by Conversation/Task/Version, and is linked from the final Checkpoint.
- Model tools have closed schemas; Core/user-only commands and absent health/browser executors are not model-visible.

- [x] **Step 1: Write failing template and complete-loop tests**

Cover unknown manager, lockfile conflict, command injection in package scripts, dependency failure, review/repair retry, runtime crash/recovery, endpoint rebinding, static fallback, and accept/discard invariants.

- [x] **Step 2: Implement typed templates and orchestration state machine**

- [x] **Step 3: Wire strict ToolDefinitions and Assistant adapters**

Replace all permissive `additionalProperties: true` project execution schemas. Remove ghost tools whose executor is absent.

- [x] **Step 4: Run local and Cloud project closure tests and Desktop workflows**

- [x] **Step 5: Commit**

Verification on 2026-07-11: `scripts/test-all.ps1 -SkipDocker` exited 0 with Sandbox Runner 27 passed/1 Windows-only POSIX process-group check skipped, Core 511, Capabilities 104, Cloud 84 unit tests/22 Docker integration tests deselected, Vitest 69, and Playwright 19 tests passing. Ruff format/check, source boundaries, offline Alembic upgrade and full downgrade through head `20260711_0015`, Rust fmt/clippy/tests, desktop build, generated-contract drift, and performance gates passed; Core readiness was 815.8 ms and initial renderer gzip was 131.3 KiB. Real Docker/PostgreSQL/S3/OCI and WSL attestation were not executed and are not reported as passed.

Commit: `feat(v3): complete project execution closure`

---

### Task 47: Build Fenced Dynamic WSL/OCI Runtime and Runtime Review

**Files:**
- Create: `core/src/fairy_core/runtime/templates.py`
- Create: `core/src/fairy_core/runtime/supervisor.py`
- Create: `core/tests/runtime/test_dynamic_templates.py`
- Create: `core/tests/runtime/test_dynamic_lifecycle.py`
- Create: `cloud/src/fairy_cloud/runtime/*`
- Create: `cloud/migrations/versions/*_runtime_leases.py`
- Create: `cloud/tests/integration/test_postgres_dynamic_runtime.py`
- Modify: `core/src/fairy_core/runtime/models.py`
- Modify: `core/src/fairy_core/runtime/ports.py`
- Modify: `core/src/fairy_core/application/runtime.py`
- Modify: `core/src/fairy_core/execution/application.py`
- Modify: `core/src/fairy_core/transports/stdio.py`
- Modify: `cloud/src/fairy_cloud/dispatchers.py`
- Modify: `deploy/compose.yaml`
- Modify: `docs/threat-model.md`

**Interfaces:**
- `RuntimeTemplate` selects a fixed argv and working directory from immutable manifests; model input never supplies a command, host, port, URL, or process environment.
- Local WSL and cloud OCI Runtime supervisors own a durable lease and fence, isolate a Version archive, expose only an attested endpoint, and support start/probe/stop/recovery without reusing one-shot Command Sandbox jobs as daemons.
- Cloud Preview URLs are opaque HTTPS proxy routes bound to tenant, Conversation, Task, Version, Runtime, lease fence, and expiry; direct worker endpoints are never sent to the renderer.
- Runtime health and browser checks produce typed `REPORT`/`SCREENSHOT` Artifacts and join the Checkpoint only when they match the current workspace generation and Preview manifest.
- `UnavailableRuntimeExecutor` remains fail-closed unless the corresponding supervisor and route are actually configured and healthy.

- [x] **Step 1: Write failing local/cloud lifecycle, endpoint, recovery, and security tests**

- [x] **Step 2: Implement typed dynamic templates and fenced Runtime supervisors**

- [x] **Step 3: Implement cloud Runtime leases, proxy routing, health, and browser Review**

- [x] **Step 4: Run local fake/real WSL gates and cloud fake/real Docker gates truthfully**

Verification on 2026-07-12: Core, Cloud unit, Sandbox simulation, Desktop, Rust,
contract generation, and offline migration gates passed. Docker/Podman are not
installed and WSL has no distribution, so real PostgreSQL 18, OCI Runtime,
Chromium-in-container, seccomp/bwrap, and FairySandbox gates were not executed.

- [ ] **Step 5: Commit**

Commit remains pending because this session cannot create the worktree Git
metadata lock. The implementation and verification are preserved in the
working tree.

Commit: `feat(v3): add fenced dynamic project runtimes`

---

### Task 48: Persist Desktop Permission UX and Generated Command Metadata

**Files:**
- Modify: `desktop/src/app/workspaceModel.ts`
- Modify: `desktop/src/app/WorkspaceShell.tsx`
- Modify: `desktop/src/chat/slashCommands.ts`
- Modify: `desktop/src/chat/ChatWorkspace.tsx`
- Modify: `desktop/src/core/client.ts`
- Modify: `desktop/src/app/*.test.tsx`
- Modify: `desktop/src/chat/*.test.tsx`
- Modify: `desktop/e2e/*.spec.ts`

**Interfaces:**
- Permission selection reads/writes Core `permissions` with revision conflict handling; local storage is no longer authority.
- Capability toggles render from generated ToolDefinition metadata and current effective manifest.
- Slash command availability is driven by Core metadata; parsing remains exact and never routes natural language by keyword.

- [x] **Step 1: Write failing permission/toggle/offline/conflict tests**

- [x] **Step 2: Replace local authority with Core settings and generated metadata**

- [x] **Step 3: Add approval, sandbox unavailable, execution progress, recovery, and conflict Playwright flows**

- [x] **Step 4: Run Vitest, production Playwright, and TypeScript/build**

Verification on 2026-07-12 passed with 541 Core tests, 103 Cloud unit tests,
74 Vitest tests, 22 production Playwright workflows, generated contracts,
TypeScript, and the production build. Commit remains pending under the same
read-only Git metadata restriction recorded in Task 47.

- [ ] **Step 5: Commit**

Commit: `feat(v3): connect governed execution controls`

---

### Task 49: Add Governed Skills and MCP Extension Runtime

**Files:**
- Create: `core/src/fairy_core/skills/*`
- Create: `core/src/fairy_core/mcp/*`
- Create: `core/tests/skills/*`
- Create: `core/tests/mcp/*`
- Create: `cloud/src/fairy_cloud/mcp/*`
- Modify: `core/src/fairy_core/commanding/registry.py`
- Modify: `core/src/fairy_core/application/service.py`
- Modify: `core/src/fairy_core/assistant/application.py`
- Modify: `desktop/src/settings/*`
- Modify: `docs/architecture.md`
- Modify: `docs/threat-model.md`

**Interfaces:**
- Versioned `SkillManifest` packages declare instructions, input contracts, required capabilities, compatible MCP servers, and provenance; Skills do not execute processes, hold project state, or bypass Core policy.
- MCP servers use explicit trust configuration and standards-current local/cloud transports. Imported tools are namespaced, schema-sanitized, size-bounded, and materialized as `ToolDefinition` records before Agent exposure.
- Every MCP call flows through Scope injection, Capability Manifest, Policy Matrix, Approval, CommandRun/Event, cancellation, idempotency, and Artifact output handling. Model-supplied Scope identity, path, endpoint, credential, and capability fields are rejected.
- Server credentials and transport details remain outside model context. Disconnects, restarts, schema drift, duplicate responses, and uncertain side effects recover fail-closed from the durable ledger.
- Fairy Skills are product capabilities and remain isolated from Codex's local skill system.

- [x] **Step 1: Verify the current official MCP specification and write Architecture/Threat ADRs**

Verified on 2026-07-12 against MCP revision `2025-11-25`, Agent Skills, and the
official Python SDK. Stable `mcp 1.28.1` is selected with `<2`; v2 is still beta.
ADR 0009 fixes the trust, transport, schema-drift, recovery, and product-Skill
boundaries. The legacy HTTP+SSE transport is excluded.

- [x] **Step 2: Write failing Skill manifest, MCP lifecycle, policy, and malicious-server tests**

- [x] **Step 3: Implement local and cloud governed adapters plus desktop settings**

- [x] **Step 4: Run contract, disconnect/recovery, scope-injection, and permission matrices**

Verification on 2026-07-12: Core `570` tests passed; Cloud `109` tests passed
with `27` Docker/PostgreSQL/S3 integration tests explicitly skipped. Ruff
check/format, source boundaries, Core/Cloud lock checks, generated OpenAPI and
TypeScript, offline Alembic full upgrade/downgrade through `20260712_0017`,
TypeScript/production build, `75` Vitest tests, and `23` Playwright workflows
passed. Official MCP SDK contract tests exercised real stdio and Streamable
HTTP servers. Recovery tests covered deletion tombstones, process-loss
uncertainty, deterministic failed replay, cancellation, schema drift, Registry
snapshot consistency, private-address blocking, and Cloud hostname allowlists.
Desktop screenshots at 1000x760 and 720x700 passed panel boundary and overflow
checks. The PostgreSQL MCP RLS/tombstone test is present but was not executed:
Docker CLI, Docker Desktop, and Podman are absent. `wsl.exe` reports that WSL
is not installed, so no FairySandbox claim is made.

- [ ] **Step 5: Commit**

Commit is pending because this session cannot write the worktree Git metadata.

Commit: `feat(v3): add governed skills and mcp extensions`

---

### Task 50: Requirement-by-requirement Acceptance, Cleanup, and Truthful Release Evidence

**Files:**
- Create: `docs/completion-audit.md`
- Create: `docs/adr/0008-brokerless-sandbox-execution.md`
- Modify: `README.md`
- Modify: `core/README.md`
- Modify: `cloud/README.md`
- Modify: `desktop/README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/threat-model.md`
- Modify: all earlier plan checkboxes and verification records to match actual evidence
- Modify: `scripts/check_boundaries.py`
- Modify: `scripts/test-all.ps1`

**Interfaces:**
- `docs/completion-audit.md` maps every original numbered Implementation Change, Public Contract, Test Plan item, non-Legacy capability, and performance gate to exact implementation and fresh test/runtime evidence.

- [x] **Step 1: Re-read the original architecture attachment and V3 plan and construct the evidence matrix**

Classify every row as proven, contradicted, missing, or environment-blocked. A collected/skipped test is not proof.

- [x] **Step 2: Remove stale deferrals, ghost metadata, duplicate authority, oversized/mixed modules, empty trees, and compatibility leakage**

`docs/completion-audit.md` now maps the full plan and capability matrix.
Historical milestone checklists are reconciled to their commits, stale Runtime
descriptions are corrected, open ToolDefinition defaults are removed, source
boundaries remain below their size limits, and 74 generated cache directories
were removed. Four pre-existing `.pytest_cache` directories are ACL-protected
by the desktop sandbox and remain ignored; the release gate disables future
pytest/Ruff/Hypothesis source-tree caches.

- [x] **Step 3: Run adversarial static scans and single-head migration checks**

Include secret literals, host process APIs, shell flags, Docker socket/mounts, renderer filesystem/process APIs, model Scope fields, permissive execution schemas, keyword routers, browser speech synthesis, duplicate Memory authority, and unowned source files.

- [x] **Step 4: Run the complete release gate fresh**

Run `scripts/test-all.ps1`, real Docker integration when available, real WSL attestation when available, Ruff/format, Rust fmt/clippy/tests, Vitest, production Playwright, generated-contract drift, Alembic upgrade/downgrade, single head, performance budgets, `git diff --check`, and clean status.

Fresh verification on 2026-07-12: `scripts/test-all.ps1 -SkipDocker` exited 0.
Sandbox Runner passed 45 with one Windows-inapplicable POSIX case skipped;
Core passed 572, Capabilities 104, Cloud 109 with 27 Docker integration tests
deselected, Vitest 75, and production Playwright 23. Ruff, boundaries, lock
checks, full offline Alembic upgrade/downgrade through `20260712_0017`, one
migration head, Rust fmt/clippy/tests, TypeScript/build, deterministic contract
regeneration, and `git diff --check` passed. Core readiness was 1127.2ms and
initial renderer gzip was 140.1KiB. Browser shell-interactive and event p95
tests passed their 1.5-second and 100ms gates.

- [x] **Step 5: Record exact environment evidence**

Do not mark Docker/PostgreSQL/S3/OCI or WSL tests passed unless their commands executed successfully in this Task.

Exact environment evidence is recorded in `docs/completion-audit.md`. Docker,
Docker Desktop, and Podman are absent; `wsl.exe` reports that WSL is not
installed. Real PostgreSQL/S3/OCI and FairySandbox gates were not executed.
The worktree Git metadata is read-only to this session.

- [ ] **Step 6: Commit**

Final attempt on 2026-07-12: `git add --all -- .` failed because Git could not
create `.git/worktrees/fairy-v3/index.lock` (`Permission denied`). No files
were staged and no commit was created.

Commit: `chore(v3): close full architecture acceptance`

## Self-review Record

- **Spec coverage:** Task 41 covers Core-owned device/Cloud permissions and capability generation; Task 42 covers generic approval and durable resume; Task 43 covers persisted Workspace, Project Index, project reads, Artifact ownership, and governed Changesets; Tasks 44-45 cover actual local/cloud command execution; Task 46 covers dependencies, executable Review, repair, static Preview evidence, and checkpoint; Task 47 covers dynamic Runtime and runtime/browser Review; Task 48 covers the desktop control surface; Task 49 covers governed Skills/MCP extensibility; Task 50 covers all original contracts, non-Legacy capability acceptance, cleanup, performance, and environment truth.
- **Known contradiction removed:** Task 47 replaces the Cloud dynamic `UnavailableRuntimeExecutor` path with fenced PostgreSQL/OCI Runtime dispatch and a private Review gateway. Task 49 now provides governed Skills/MCP through the same Registry, Scope, policy, approval, CommandRun, cancellation, and Artifact authority rather than a second execution path.
- **Type consistency:** `EffectiveExecutionPolicy` feeds both model exposure and Command Bus policy; `SandboxRequest` is Core-injected and is consumed by WSL and Cloud executors; generic Approval links one CommandRun; Task Workspace/Project Index precede project tool execution.
- **Placeholder scan:** No implementation step uses TBD, TODO, implement-later, or an unspecified executor. Environment-dependent tests have exact commands and explicit skip semantics.
