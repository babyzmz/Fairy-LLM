# Fairy V3 Durable Runtime and Preview Design

- Status: approved by the Fairy V3 architecture plan
- Date: 2026-07-11
- Scope: durable Runtime, Preview, Artifact, static local Preview, recovery, and WSL boundary

## Intent

The current local project loop can isolate and review a Version, but the
Preview shown by Desktop is sample UI rather than Core-owned state. This slice
turns Preview into a durable, task-scoped product result without exposing a
host shell or pretending that WSL is available.

The first executable runtime is a read-only static-site server implemented by
the Rust local worker. It serves a validated Fairy-managed Version directory
on loopback and never executes a project binary; browser script execution is
constrained by the Preview iframe and response policy. Dynamic applications,
dependency installation, reviews that execute project tools, and generic shell
commands require the dedicated WSL2 `FairySandbox` or a cloud OCI Worker.

## Goals

1. Persist RuntimeSession, PreviewSession, and Artifact as tenant-scoped Core
   entities with strict state machines and immutable Scope identity.
2. Make Preview start, stop, status, resolution, and recovery cross the Command
   Bus and durable ledger.
3. Provide one safe local static Preview that works without WSL or Docker.
4. Keep dynamic execution and `run.sandboxed` disabled unless a separately
   attested sandbox is healthy.
5. Expose Runtime, Preview, and Artifact through the shared CoreService,
   local JSON-RPC, Cloud REST, generated TypeScript, and CoreClient.
6. Replace Desktop sample Preview state with public Core state and durable
   events in a later UI task of this milestone.

## Non-goals

- Executing `npm`, Python, shell, build, or user project binaries on Windows.
- Installing or enabling WSL automatically.
- Treating a loopback static server as a healthy generic sandbox.
- Rendering arbitrary remote URLs in Preview.
- Capturing screenshots before the browser review adapter exists.
- Implementing the cloud OCI execution Worker in this local slice.

## Invariants

1. Runtime, Preview, and Artifact carry tenant, Project, Conversation, Task,
   Version, and project-root provenance where applicable.
2. Core creates IDs and injects Scope. Renderer, Agent, and model-supplied IDs,
   paths, ports, process handles, URLs, and health are untrusted.
3. A Preview is bound to one Runtime and Version and cannot be rebound.
4. One Task may have at most one non-terminal Preview. Retrying start returns
   the same Preview and Runtime or resumes their recovery path.
5. A Preview URL is accepted only from a registered executor result, must use
   `http`, and must target loopback for local execution.
6. External start intent is committed before dispatch. Completion or failure is
   committed after the idempotent executor returns.
7. Core restart probes `starting`, `ready`, and `stopping` sessions. It never
   marks an unprobed process healthy.
8. Discarding a Version stops its active Preview first. A failed stop blocks
   destructive cleanup and reports `WORKER_INTERRUPTED`. Accepting a Version
   may promote its ready Preview to Project active Preview without restart.
9. Static Preview serves read-only bytes from one canonical Version root. It
   rejects symlinks, junction/reparse points, traversal, encoded traversal,
   UNC/device paths, directory escape, mutation methods, and non-loopback bind.
10. Static Preview does not change the `run.sandboxed` capability state.

## Domain model

### RuntimeSession

Fields: ID, Project/Conversation/Task/Version IDs, project root, execution
target, runtime kind, executor name, executor handle, selected port, status,
health, error code, idempotency key, timestamps, and monotonic revision.

Statuses:

```text
created -> starting -> running -> stopping -> stopped
                    -> failed
created/starting/running/stopping -> interrupted
interrupted -> starting | stopping | failed
failed -> starting
```

Only Core transitions status. `executor_handle` and port are absent until a
successful start result and are cleared only after a confirmed stop.

### PreviewSession

Fields: ID, Runtime ID, Project/Conversation/Task/Version IDs, project root,
URL, visibility, status, health, error code, idempotency key, timestamps, and
revision.

Statuses:

```text
created -> starting -> ready -> stopping -> stopped
                    -> failed
created/starting/ready/stopping -> interrupted
interrupted -> starting | stopping | failed
failed -> starting
```

The URL exists only in `ready`, `stopping`, or recoverable `interrupted` state.
Core updates Conversation active Preview when ready. Project active Preview is
updated only when its Version is explicitly accepted.

### Artifact

Artifacts are immutable manifests. This slice stores Preview metadata and
future capture references: ID, Scope IDs, type, visibility, storage location,
media type, byte length, SHA-256, metadata, and creation time. Payload bytes
remain behind `ArtifactStore`; database rows do not embed files.

## Runtime kinds

### `static_site`

Core selects this kind only when the target Version contains a regular
`index.html` within the Version root and no reparse point is traversed. The
Rust worker binds `127.0.0.1:0`, returns the assigned port and an opaque
executor handle, and serves GET/HEAD only. Directory requests resolve to
`index.html`; missing files return 404; unsupported methods return 405.

The worker validates every request path after percent decoding and
canonicalization. Cache is disabled for HTML and bounded for immutable media.
The response includes `X-Content-Type-Options: nosniff`, a restrictive default
CSP, and no directory listing. Stop is idempotent.

### `wsl_project`

This runtime kind remains unavailable until `FairySandbox` attestation passes.
Health requires WSL 2, the exact imported distribution name, a non-root
`fairy` user, the packaged runner version, and `/etc/wsl.conf` proving:

```ini
[automount]
enabled=false
mountFsTab=false

[interop]
enabled=false
appendWindowsPath=false

[user]
default=fairy
```

Microsoft documents per-distribution `wsl.conf`, disabling fixed-drive
automount, disabling Windows process interop/path injection, and importing a
custom root filesystem with `wsl --import`:

- <https://learn.microsoft.com/en-us/windows/wsl/wsl-config>
- <https://learn.microsoft.com/en-us/windows/wsl/basic-commands>
- <https://learn.microsoft.com/en-us/windows/wsl/use-custom-distro>

Fairy never mounts a Windows project drive into the distro. A future adapter
will stream a scoped workspace snapshot into ext4 storage and return only
declared artifacts/changesets. WSL installation, distribution import, and
Hyper-V firewall changes require an explicit user/admin setup flow.

## Ports

```python
class RuntimeExecutor(Protocol):
    def health(self) -> RuntimeExecutorHealth: ...
    def start_static(self, request: StaticRuntimeStart) -> RuntimeStartResult: ...
    def probe(self, executor_handle: str) -> RuntimeProbeResult: ...
    def stop(self, executor_handle: str) -> RuntimeStopResult: ...

class ArtifactStore(Protocol):
    def put(self, *, key: str, content: bytes, media_type: str) -> StoredArtifact: ...
    def get(self, *, key: str) -> StoredArtifact | None: ...
```

The Rust local worker implements `RuntimeExecutor` through typed JSON-RPC.
Cloud later supplies an OCI implementation. Core application code depends only
on the ports.

## Persistence

SQLite and PostgreSQL use the same `core_runtime_sessions`,
`core_preview_sessions`, and `core_artifacts` tables. Every primary and foreign
key includes tenant identity. PostgreSQL enables and forces RLS. Runtime and
Preview idempotency keys are unique per tenant. Partial uniqueness permits one
non-terminal Preview per Task. SQLite schema initialization and Alembic upgrade
and downgrade remain equivalent.

## Command flow

Preview start:

```text
validate Task/Version/Scope and static entry
  -> create/reuse Runtime + Preview in created
  -> append preview.start CommandRun and starting events
  -> commit durable intent
  -> executor.start_static(runtime ID, Preview ID, managed root)
  -> validate loopback URL/handle/port
  -> persist Runtime running + Preview ready + active Preview pointers
  -> finish CommandRun and append visible preview.ready event
```

Preview stop follows the same split around `executor.stop`. A duplicate start,
stop, or recovery call is idempotent by entity ID and executor handle.

If dispatch is interrupted after intent, recovery probes the handle. A running
process completes the original start; an absent process marks both entities
interrupted and permits a fenced restart. Unknown health never becomes ready.

## Public contracts

CoreClient adds:

- `runtimes.get({ runtime_id })`
- `runtimes.health({ task_id })`
- `previews.start({ task_id, idempotency_key })`
- `previews.get({ preview_id })`
- `previews.resolve({ conversation_id })`
- `previews.stop({ preview_id, idempotency_key })`
- `artifacts.list({ task_id })`
- `artifacts.read({ artifact_id })`

The local and cloud transports use the same request/response models and
contract tests. Preview resolution order is explicit Preview ID, current Task,
Conversation draft, Conversation base Version, Project active Version, then no
Preview. It never selects the most recently created Preview by Project alone.

## Desktop behavior

The Context Bar reads Project, Conversation, Version, execution target,
permission, and sync state from Core. Task Timeline renders user-visible ledger
events. Preview renders an iframe only for a Core-resolved loopback/cloud URL,
with loading, unavailable, interrupted, failed, stopped, and ready states.
Approval and Version controls call CoreClient and refresh from durable events.
Developer Mode may show executor, port, source root, and command diagnostics.

## Verification

1. Property tests reject every illegal Runtime and Preview transition.
2. SQLite/PostgreSQL contract tests cover same-ID tenant isolation,
   idempotency, active-Preview uniqueness, and migration reversal.
3. Rust tests cover traversal, percent encoding, reparse/symlink escape,
   methods, MIME handling, loopback binding, duplicate start/stop, and process
   interruption.
4. Recovery tests crash before dispatch, after bind, after executor start,
   after ready persistence, and during stop without duplicating servers.
5. Capability tests prove a static Preview does not enable `run.sandboxed` and
   missing/unattested WSL returns `SANDBOX_UNAVAILABLE`.
6. Contract tests run the same Preview lifecycle over JSON-RPC and REST.
7. Playwright covers ready, loading, stopped, unavailable, offline, approval,
   accept, discard, and reduced-motion UI states.
8. The complete gate runs Core/Cloud/Rust/Desktop, migration SQL, generated
   contracts, and Docker integration when available. WSL integration is a
   separate explicit gate and cannot be claimed on a machine without the
   `FairySandbox` distribution.

## Delivery tasks

1. Runtime/Preview/Artifact domain contracts and state machines.
2. Tenant-scoped persistence and reversible cloud migration.
3. RuntimeExecutor port and Rust read-only static server.
4. Durable Core start/stop/resolve/recovery orchestration.
5. Shared CoreService, REST, generated client, and transport contract tests.
6. Desktop durable Timeline/Preview integration.
7. WSL health attestation and fail-closed capability wiring.
8. Security/recovery/PostgreSQL/Playwright gates and milestone cleanup.
