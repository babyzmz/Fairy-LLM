# Verification and Acceptance Policy

Passing tests alone does not complete a task. Completion requires engineering gates,
user-observable acceptance, required real-environment verification, and regression
coverage.

## Before implementation

For work that spans modules or changes scoped state, asynchronous work, persistence,
UI interaction, or native windows:

1. Read the requirement, relevant implementation, and existing tests.
2. Create or update `docs/acceptance/<task-name>.md`.
3. Record observable behavior, invariants, state ownership and scope keys, risks,
   acceptance scenarios, required environments, automation boundaries, and evidence.
4. Do not begin broad implementation until each important behavior has a concrete
   acceptance method.
5. State assumptions instead of silently expanding ambiguous scope.

## Bug fixes

Use this order for every bug:

1. Reproduce it before changing production code.
2. Record deterministic reproduction steps and evidence.
3. Add a regression test that fails for the same root cause.
4. Confirm the test fails before the fix.
5. Implement the smallest root-cause fix.
6. Confirm the regression passes.
7. Run affected-area regression tests.
8. Run the necessary full regression once, at the end.

When automation is unavailable, preserve a precise manual acceptance script and its
evidence. Unrelated unit tests cannot substitute for the missing environment.

## Risk-driven acceptance

### Scoped state

Changes involving users, tenants, workspaces, projects, conversations, turns, or
artifacts must use at least two distinct entities and verify bidirectional isolation,
rapid switching, late events or responses, and refresh or restart recovery. A
single-entity fixture cannot prove isolation.

### Asynchronous state

Changes involving streams, polling, jobs, loading, traces, approvals, or recovery
must cover success, failure, cancellation, interruption, and timeout. Terminal state
must stop loading, polling, and subscriptions. Old responses and late events must not
overwrite the active scope. Verify crash or restart recovery where applicable.

### UI and input

Clickable controls, inputs, overlays, and drag surfaces require hit-target and focus
checks in addition to screenshots. Verify keyboard behavior, IME, constrained sizes,
and supported scale factors. If visual and interactive layers differ, assert their
bounding boxes. A screenshot alone is not interaction evidence.

### Native desktop behavior

Tauri windows, transparent regions, drag behavior, tray behavior, multi-window
lifecycle, and DPI must be accepted in a real Tauri runtime. Vite or Chromium tests
cannot replace native window position, lifecycle, hit-test, focus, or DPI evidence.
If that environment is unavailable, report the item as unverified.

### Persistence and caches

Database, local storage, cache, and recovery changes require close-and-reopen
verification plus old, missing, and partial data cases. Global stores and caches must
be exercised across multiple entities and against stale async completion.

## Test truthfulness

Every acceptance plan must identify mocked boundaries, explain why they do not hide
the risk, identify boundaries that require real implementations, and list skipped or
unautomated behavior. Never report skipped, unrun, or environment-blocked checks as
passed.

Existing assertions may change only when the accepted product specification changed,
not to accommodate a regression. The final report must list every modified existing
test and why it changed.

## Execution order

1. Fast structural preflight.
2. Direct unit tests.
3. Relevant integration tests.
4. Task-specific behavioral acceptance.
5. Browser or native E2E, as required.
6. Affected-package regression.
7. One necessary repository-wide regression.

Stop before long suites when structural preflight fails.

## Completion

Do not declare completion until every acceptance invariant has evidence in its
required environment, all mandatory scenarios pass, skipped checks are explained,
and no unexplained cross-scope state, infinite loading, or unterminated subscription
remains. Final reports must include commands, results, native evidence where required,
unverified items, and residual risk.

