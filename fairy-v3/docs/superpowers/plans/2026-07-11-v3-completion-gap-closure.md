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

- [ ] **Step 1: Write failing protocol, path, environment, resource, and cancellation tests**

Cover malformed argv, NULs, overlong values, forbidden host environment keys, output floods, timeout, process tree termination, workspace generation mismatch, archive traversal, symlink escape, and attestation mismatch.

- [ ] **Step 2: Run tests and observe missing sandbox interfaces**

- [ ] **Step 3: Implement the runner and streamed managed-workspace synchronization**

The installer creates a non-root `fairy` user, disables automount/interoperability, installs the runner and isolation dependencies, and emits a signed-format health document. Workspace content crosses stdin as a bounded validated archive and is stored only in the distro ext4 filesystem.

- [ ] **Step 4: Compose local capability health and fail closed when attestation fails**

Expose `run.sandboxed`, dependency, review, and dynamic Runtime commands only after a current attestation from the matching executor.

- [ ] **Step 5: Verify simulated adapters and run the real WSL gate when available; commit**

Commit: `feat(v3): add attested wsl sandbox execution`

---

### Task 45: Add the Brokerless Cloud OCI Execution Worker

**Files:**
- Create: `cloud/src/fairy_cloud/execution/models.py`
- Create: `cloud/src/fairy_cloud/execution/repository.py`
- Create: `cloud/src/fairy_cloud/execution/executor.py`
- Create: `cloud/src/fairy_cloud/workers/execution.py`
- Create: `cloud/tests/integration/test_execution_worker.py`
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

- [ ] **Step 1: Write failing unit and real PostgreSQL tests**

Cover enqueue atomicity, RLS, duplicate key/fingerprint conflict, dual-worker claim, stale fence, crash before spawn/after spawn/after result, cancellation, scope mismatch, and no Outbox-worker project access.

- [ ] **Step 2: Implement migration/repository and run unit tests**

- [ ] **Step 3: Implement non-root execution worker and Cloud composition**

The execution service has a scoped project volume, read-only base filesystem, tmpfs, dropped capabilities, no Docker socket, resource limits, controlled egress, and a distinct database role. The Outbox service remains unchanged and mount-free.

- [ ] **Step 4: Run Docker PostgreSQL/S3/OCI integration when available**

Run: `docker compose -f cloud/compose.yaml --profile test up --build --abort-on-container-exit --exit-code-from integration integration`

- [ ] **Step 5: Commit**

Commit: `feat(v3): add cloud oci execution worker`

---

### Task 46: Complete Dependency, Review, Runtime, and Preview Orchestration

**Files:**
- Create: `core/src/fairy_core/execution/templates.py`
- Create: `core/src/fairy_core/execution/application.py`
- Create: `core/tests/execution/test_templates.py`
- Create: `core/tests/execution/test_project_closure.py`
- Modify: `core/src/fairy_core/commanding/registry.py`
- Modify: `core/src/fairy_core/application/runtime.py`
- Modify: `core/src/fairy_core/application/core.py`
- Modify: `core/src/fairy_core/application/service.py`
- Modify: `desktop/src/app/TaskTimeline.tsx`
- Modify: `desktop/src/app/PreviewPanel.tsx`
- Modify: `desktop/e2e/workspace-layout.spec.ts`

**Interfaces:**
- `ProjectExecutionApplication.install_dependencies/review/start_runtime/repair/checkpoint` selects Core-owned templates from detected manifests; it never accepts a shell string.
- Supported dependency templates are npm, pnpm, yarn, uv, pip, and Cargo with exact cwd and lockfile policy.
- Review templates implement typecheck, lint, test, build, health, and browser checks and persist structured results as Artifacts.
- Dynamic Preview endpoint comes only from a fenced WSL/OCI Runtime and is resolved by Conversation/Task/Version; static Preview remains available independently.

- [ ] **Step 1: Write failing template and complete-loop tests**

Cover unknown manager, lockfile conflict, command injection in package scripts, dependency failure, review/repair retry, runtime crash/recovery, endpoint rebinding, static fallback, and accept/discard invariants.

- [ ] **Step 2: Implement typed templates and orchestration state machine**

- [ ] **Step 3: Wire strict ToolDefinitions and Assistant adapters**

Replace all permissive `additionalProperties: true` project execution schemas. Remove ghost tools whose executor is absent.

- [ ] **Step 4: Run local and Cloud project closure tests and Desktop workflows**

- [ ] **Step 5: Commit**

Commit: `feat(v3): complete project execution closure`

---

### Task 47: Persist Desktop Permission UX and Generated Command Metadata

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

- [ ] **Step 1: Write failing permission/toggle/offline/conflict tests**

- [ ] **Step 2: Replace local authority with Core settings and generated metadata**

- [ ] **Step 3: Add approval, sandbox unavailable, execution progress, recovery, and conflict Playwright flows**

- [ ] **Step 4: Run Vitest, production Playwright, TypeScript/build, and commit**

Commit: `feat(v3): connect governed execution controls`

---

### Task 48: Requirement-by-requirement Acceptance, Cleanup, and Truthful Release Evidence

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

- [ ] **Step 1: Re-read the original architecture attachment and V3 plan and construct the evidence matrix**

Classify every row as proven, contradicted, missing, or environment-blocked. A collected/skipped test is not proof.

- [ ] **Step 2: Remove stale deferrals, ghost metadata, duplicate authority, oversized/mixed modules, empty trees, and compatibility leakage**

- [ ] **Step 3: Run adversarial static scans and single-head migration checks**

Include secret literals, host process APIs, shell flags, Docker socket/mounts, renderer filesystem/process APIs, model Scope fields, permissive execution schemas, keyword routers, browser speech synthesis, duplicate Memory authority, and unowned source files.

- [ ] **Step 4: Run the complete release gate fresh**

Run `scripts/test-all.ps1`, real Docker integration when available, real WSL attestation when available, Ruff/format, Rust fmt/clippy/tests, Vitest, production Playwright, generated-contract drift, Alembic upgrade/downgrade, single head, performance budgets, `git diff --check`, and clean status.

- [ ] **Step 5: Record exact environment evidence and commit**

Do not mark Docker/PostgreSQL/S3/OCI or WSL tests passed unless their commands executed successfully in this Task.

Commit: `chore(v3): close full architecture acceptance`

## Self-review Record

- **Spec coverage:** Task 41 covers Core-owned device/Cloud permissions and capability generation; Task 42 covers generic approval and durable resume; Task 43 covers persisted Workspace, Project Index, project reads, Artifact ownership, and governed Changesets; Tasks 44-45 cover actual local/cloud execution; Task 46 covers dependencies, Review, Runtime, Preview, repair, and checkpoint; Task 47 covers the desktop control surface; Task 48 covers all original contracts, non-Legacy capability acceptance, cleanup, performance, and environment truth.
- **Known contradiction being removed:** The current Cloud composition uses `UnavailableRuntimeExecutor`, local Assistant execution hardcodes standard policy and unhealthy sandbox, and several model-visible project tools have no executor. Existing green tests do not prove those requirements.
- **Type consistency:** `EffectiveExecutionPolicy` feeds both model exposure and Command Bus policy; `SandboxRequest` is Core-injected and is consumed by WSL and Cloud executors; generic Approval links one CommandRun; Task Workspace/Project Index precede project tool execution.
- **Placeholder scan:** No implementation step uses TBD, TODO, implement-later, or an unspecified executor. Environment-dependent tests have exact commands and explicit skip semantics.
