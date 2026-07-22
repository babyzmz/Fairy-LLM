# Preview Runtime Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically activate a governed website Preview when entering a chat while limiting each device to three warm Runtimes with ten-minute idle and deterministic LRU eviction.

**Architecture:** Desktop sends an idempotent activation intent for the selected Task/Workspace/Version. Core owns template detection, capacity, access metadata, protected-session filtering, stop-before-start eviction, and periodic idle reaping. Existing Runtime executors remain the only process owners.

**Tech Stack:** Python 3.13, Pydantic 2, SQLAlchemy 2, SQLite/PostgreSQL, pytest, React 19, TanStack Query, TypeScript, Vitest.

## Global Constraints

- Device-wide active Preview capacity is exactly 3.
- Warm idle timeout is exactly 10 minutes.
- A selected, starting, stopping, or actively executing Task Preview is never evicted.
- React never executes Shell, package-manager, or process commands.
- Existing trusted static, Vite, Next, Astro, Node, Python ASGI, and RuntimeGraph adapters remain authoritative.
- Preview activation is not model-visible.
- Development validation does not build a Tauri release or Docker image.

---

### Task 1: Durable Preview access metadata

**Files:**
- Modify: `core/src/fairy_core/domain/execution.py`
- Modify: `core/src/fairy_core/storage/schema.py`
- Modify: `core/src/fairy_core/storage/ports.py`
- Modify: `core/src/fairy_core/storage/execution_store.py`
- Modify: `core/src/fairy_core/storage/sqlite_migrations.py`
- Modify: `core/src/fairy_core/storage/sqlite.py`
- Modify: `core/src/fairy_core/persistence/sqlite_split_migration.py`
- Create: `cloud/migrations/versions/20260722_0040_preview_runtime_pool.py`
- Modify: `core/src/fairy_core/contracts/runtime.py`
- Test: `core/tests/test_execution_domain.py`
- Test: `core/tests/assistant/test_repository_contract.py`
- Test: `cloud/tests/test_postgres_contract.py`

**Interfaces:**
- Produces: `PreviewSession.last_accessed_at: datetime`.
- Produces: `StateStore.active_previews() -> list[PreviewSession]`.
- Produces: `StateStore.touch_preview_access(preview_id: UUID, accessed_at: datetime) -> PreviewSession`.

- [ ] **Step 1: Write failing domain and repository tests**

```python
def test_preview_access_timestamp_is_utc_and_not_before_creation(stack):
    preview = stack.preview
    assert preview.last_accessed_at == preview.created_at

def test_touch_preview_access_does_not_advance_domain_revision(stack):
    before = stack.preview.revision
    touched = stack.state.touch_preview_access(stack.preview.id, accessed_at=later)
    assert touched.last_accessed_at == later
    assert touched.revision == before
```

- [ ] **Step 2: Run focused tests and verify missing-field failures**

Run: `uv run pytest core/tests/test_execution_domain.py core/tests/assistant/test_repository_contract.py -q`

Expected: FAIL because `last_accessed_at`, `active_previews`, and `touch_preview_access` do not exist.

- [ ] **Step 3: Add the domain field and monotonic store operations**

```python
@dataclass(frozen=True, slots=True)
class PreviewSession:
    # existing fields
    last_accessed_at: datetime

    def __post_init__(self) -> None:
        _validate_timestamps(self.created_at, self.updated_at)
        _validate_timestamps(self.created_at, self.last_accessed_at)
```

`touch_preview_access` updates only `last_accessed_at` using
`max(existing, requested)` and does not increment Preview revision. Query active
Previews in `created`, `starting`, `ready`, or `stopping` status ordered by
`last_accessed_at, id`.

- [ ] **Step 4: Add SQLite and PostgreSQL migrations**

Backfill `last_accessed_at = updated_at`, make it non-null, and create
`ix_core_preview_sessions_tenant_active_access` over tenant, status, access
time, and ID. Add the migration to both local database initialization paths.

- [ ] **Step 5: Run persistence and migration tests**

Run: `uv run pytest core/tests/test_execution_domain.py core/tests/assistant/test_repository_contract.py cloud/tests/test_postgres_contract.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```text
feat(runtime): persist preview access metadata
```

### Task 2: Core capacity and LRU activation

**Files:**
- Create: `core/src/fairy_core/runtime/pool.py`
- Modify: `core/src/fairy_core/application/runtime_contracts.py`
- Modify: `core/src/fairy_core/application/runtime.py`
- Modify: `core/src/fairy_core/application/runtime_support.py`
- Test: `core/tests/runtime/test_preview_pool.py`
- Test: `core/tests/test_runtime_application.py`

**Interfaces:**
- Produces: `PREVIEW_RUNTIME_CAPACITY = 3`.
- Produces: `PREVIEW_IDLE_TIMEOUT = timedelta(minutes=10)`.
- Produces: `PreviewActivationOutcome` and `PreviewActivationResult`.
- Produces: `RuntimeApplication.activate_preview(request) -> PreviewActivationResult`.
- Produces: `RuntimeApplication.reap_idle_previews(now) -> tuple[UUID, ...]`.

- [ ] **Step 1: Write failing policy tests**

```python
def test_fourth_activation_stops_oldest_eligible_preview(pool_stack):
    first, second, third = pool_stack.start_three()
    result = pool_stack.activate_fourth()
    assert result.evicted_preview_id == first.id
    assert pool_stack.executor.stopped == [first.runtime_id]
    assert result.active_count == 3

def test_active_task_is_not_an_lru_victim(pool_stack):
    protected = pool_stack.oldest(task_status=TaskStatus.EXECUTING)
    result = pool_stack.activate_fourth()
    assert result.evicted_preview_id != protected.id
```

- [ ] **Step 2: Run tests and verify capacity behavior is absent**

Run: `uv run pytest core/tests/runtime/test_preview_pool.py -q`

Expected: FAIL because Runtime pool policy and activation do not exist.

- [ ] **Step 3: Implement pure victim selection**

```python
PREVIEW_RUNTIME_CAPACITY = 3
PREVIEW_IDLE_TIMEOUT = timedelta(minutes=10)

def select_lru_victim(
    previews: Sequence[PreviewSession],
    *,
    selected_conversation_id: UUID,
    protected_task_ids: Collection[UUID],
) -> PreviewSession | None:
    eligible = (
        preview for preview in previews
        if preview.conversation_id != selected_conversation_id
        and preview.task_id not in protected_task_ids
        and preview.status is PreviewStatus.READY
    )
    return min(eligible, key=lambda item: (item.last_accessed_at, item.id), default=None)
```

- [ ] **Step 4: Implement stop-before-start activation**

Activation validates Scope and template before eviction. It reuses and touches
a ready matching Preview; starts a fresh Preview for a terminal predecessor;
and returns `not_runnable` for `RuntimeTemplateError` indicating no supported
entrypoint. When capacity is full it stops one victim through the existing
durable `_stop_preview` flow before calling `_start_preview`.

Allow recreation for immutable `READY` and `ACCEPTED` Tasks without changing
Task status. Visibility is `PROJECT_ACTIVE` only for an accepted Project
Version; all other new sessions remain `CHAT_DRAFT`.

- [ ] **Step 5: Implement idle reaping**

`reap_idle_previews` finds ready, unprotected sessions older than ten minutes
and stops each with a Core-owned idempotency key. A failed stop aborts that
victim without starting a replacement or corrupting another Runtime.

- [ ] **Step 6: Run Runtime tests**

Run: `uv run pytest core/tests/runtime/test_preview_pool.py core/tests/test_runtime_application.py core/tests/runtime/test_dynamic_lifecycle.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```text
feat(runtime): add bounded preview activation pool
```

### Task 3: Activation RPC and idle scheduler

**Files:**
- Create: `core/src/fairy_core/runtime/pool_scheduler.py`
- Modify: `core/src/fairy_core/contracts/models.py`
- Modify: `core/src/fairy_core/contracts/methods.py`
- Modify: `core/src/fairy_core/application/runtime_service.py`
- Modify: `core/src/fairy_core/application/service.py`
- Modify: `contracts/openapi.json`
- Modify: `contracts/rpc-methods.json`
- Modify: `desktop/src/core/generated/api.d.ts`
- Modify: `desktop/src/core/generated/rpcMethods.ts`
- Modify: `desktop/src/core/client.ts`
- Test: `core/tests/test_core_service.py`
- Test: `core/tests/test_jsonrpc_transport.py`
- Test: `core/tests/runtime/test_pool_scheduler.py`

**Interfaces:**
- Produces: `previews.activate` local-and-cloud Core method.
- Produces: `RuntimePoolScheduler.close()`.

- [ ] **Step 1: Write failing contract and scheduler tests**

```python
def test_preview_activate_is_public_but_not_model_visible():
    method = CORE_METHODS["previews.activate"]
    assert method.transport.value == "local_and_cloud"
    assert "previews.activate" not in model_tool_names()

def test_scheduler_reaps_on_interval(fake_runtime, fake_clock):
    scheduler = RuntimePoolScheduler(fake_runtime, interval_seconds=30)
    fake_clock.advance(minutes=10)
    assert fake_runtime.reap_calls == 1
    scheduler.close()
```

- [ ] **Step 2: Run focused tests and verify missing RPC failures**

Run: `uv run pytest core/tests/test_core_service.py core/tests/test_jsonrpc_transport.py core/tests/runtime/test_pool_scheduler.py -q`

Expected: FAIL because the method and scheduler are absent.

- [ ] **Step 3: Add Pydantic activation contracts and handler**

The input carries Task, Workspace, Version, expected Workspace revision, and a
bounded idempotency key. The output carries outcome, optional Preview context,
adapter, capacity, active count, optional evicted ID, and public reason.

- [ ] **Step 4: Add scheduler lifecycle**

Create the scheduler after `RuntimeApplication`, wake it on successful
activation, run a bounded 30-second sweep, and close it before external Runtime
resources. Scheduler exceptions are contained and retried on the next sweep.

- [ ] **Step 5: Regenerate contracts**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/generate-contracts.ps1`

Expected: OpenAPI, RPC manifest, and TypeScript client declarations change only
for Preview activation and `last_accessed_at`.

- [ ] **Step 6: Run contracts and transport tests**

Run: `uv run pytest core/tests/test_core_service.py core/tests/test_jsonrpc_transport.py core/tests/runtime/test_pool_scheduler.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```text
feat(runtime): expose automatic preview activation
```

### Task 4: Desktop chat-entry activation and status UI

**Files:**
- Create: `desktop/src/app/usePreviewActivation.ts`
- Modify: `desktop/src/app/workspaceModel.ts`
- Modify: `desktop/src/app/workspaceTypes.ts`
- Modify: `desktop/src/app/PreviewWorkspace.tsx`
- Modify: `desktop/src/app/PreviewPanel.tsx`
- Modify: `desktop/src/app/workspace.css`
- Test: `desktop/src/app/usePreviewActivation.test.tsx`
- Test: `desktop/src/app/PreviewPanel.test.tsx`
- Test: `desktop/src/app/App.test.tsx`

**Interfaces:**
- Consumes: `CoreClient.previews.activate(input)`.
- Produces: `PreviewActivationView` with outcome, adapter, capacity, active
  count, and safe detail.

- [ ] **Step 1: Write failing selection and stale-result tests**

```tsx
it("activates once when a runnable chat identity becomes available", async () => {
  renderHook(() => usePreviewActivation(input));
  await waitFor(() => expect(client.previews.activate).toHaveBeenCalledTimes(1));
});

it("ignores an activation result for a previously selected chat", async () => {
  const hook = renderHook(({ input }) => usePreviewActivation(input), { initialProps });
  hook.rerender({ input: secondChat });
  firstRequest.resolve(firstResult);
  expect(hook.result.current.previewId).not.toBe(firstResult.preview.id);
});
```

- [ ] **Step 2: Run Vitest and verify missing-hook failures**

Run: `npm run test -- --run src/app/usePreviewActivation.test.tsx src/app/PreviewPanel.test.tsx`

Expected: FAIL because automatic activation is absent.

- [ ] **Step 3: Implement identity-deduplicated activation**

Build the identity from Conversation, Task, Workspace, Version, Workspace
revision, and a selection nonce. Abort stale presentation updates when the
identity changes. Renew activation every two minutes while selected so the
ten-minute Core idle timeout cannot reap the visible chat.

- [ ] **Step 4: Refresh bound queries after activation**

Invalidate only Preview, Runtime health, Workspace, and Inspector queries for
the selected identity. Do not switch tabs for `not_runnable`. A ready or newly
started Preview selects Preview only when the user has not explicitly selected
another Inspector tab for that chat.

- [ ] **Step 5: Add compact Runtime status**

Show adapter and slot usage in the Preview toolbar. Render distinct messages
for automatic start, waiting for slot, LRU pause, unsupported entrypoint, and
recoverable failure. Keep manual Start/Restart/Stop controls.

- [ ] **Step 6: Run Desktop tests and type check**

Run: `npm run test -- --run src/app/usePreviewActivation.test.tsx src/app/PreviewPanel.test.tsx src/app/App.test.tsx`

Run: `npm exec tsc -- --noEmit`

Expected: PASS.

- [ ] **Step 7: Commit**

```text
feat(desktop): auto-load chat preview runtimes
```

### Task 5: Full verification and process lifecycle

**Files:**
- Modify only if a verified defect is found in the preceding implementation.
- Test: `core/tests/runtime/test_preview_pool.py`
- Test: `desktop/src/app/usePreviewActivation.test.tsx`

**Interfaces:**
- Consumes all preceding contracts.
- Produces no new product contract.

- [ ] **Step 1: Run complete Core and Capabilities suites**

Run: `uv run pytest core/tests capabilities/tests -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 2: Run complete Desktop suite and static checks**

Run: `npm run test -- --run`

Run: `npm exec tsc -- --noEmit`

Run: `uv run ruff check core/src core/tests capabilities/src capabilities/tests cloud/src cloud/tests`

Expected: PASS.

- [ ] **Step 3: Run migration and contract drift checks**

Run the repository migration, OpenAPI, JSON-RPC, and architecture-boundary
tests. PostgreSQL integration is reported separately if Docker is unavailable.

- [ ] **Step 4: Restart only the development environment**

Use `scripts/start-desktop.ps1` through the existing one-click launcher. Verify
Vite HTTP 200, Core readiness, and Voice process ownership without a release
or Docker build.

- [ ] **Step 5: Perform real process-bound smoke**

Open four runnable chats. Confirm at most three active Preview Runtime records
and process groups, the first eligible Runtime stops before the fourth starts,
returning to an evicted chat restarts it, and application shutdown removes all
owned Node/Python processes.

- [ ] **Step 6: Commit any verification-only fixes**

```text
fix(runtime): close preview pool lifecycle gaps
```
