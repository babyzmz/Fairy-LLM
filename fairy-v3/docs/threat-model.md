# Fairy V3 Threat Model

## Protected assets

- user source code and documents;
- secrets, credentials, tokens, and private prompts;
- project, conversation, task, version, and artifact ownership;
- canonical Memory Claims, Observation provenance, and Tombstones;
- the integrity of Active Versions and checkpoints;
- local machine files and processes outside a scoped Version;
- cloud account data and worker results.

## Trust boundaries

The React renderer, model output, tool arguments, imported project contents,
web content, cloud event payloads, and worker output are untrusted. The Core
may trust only validated contracts, server-injected identity, persisted state,
and independently verified worker attestations/results.

## Required controls

1. Canonicalize every path after resolving links and junctions. Reject UNC,
   device paths, alternate data streams, and paths outside allowed roots.
2. Re-check path identity immediately before file replacement to limit TOCTOU
   attacks. Apply writes through atomic replacement where supported.
3. Remove inherited environment variables. Inject only explicit allowlisted
   values and never pass host credentials to a sandbox.
4. Classify every ToolDefinition by side effect and risk. Evaluate capability,
   Scope, policy, and approval independently before dispatch.
5. Persist CommandRun before execution. Use idempotency keys, leases, worker
   heartbeats, and reconciliation before retrying interrupted writes.
6. Keep the WSL2 FairySandbox isolated from Windows interop and host-drive
   automounts. Network access is off unless a typed network capability opens an
   allowlisted destination class.
7. Run the Outbox Worker and any OCI execution Worker as distinct non-root
   services with read-only base filesystems, resource limits, scoped mounts,
   and controlled egress. The Outbox Worker has no project mount or Docker
   socket and cannot be treated as an execution sandbox.
8. Store refresh credentials in the Tauri secure store; keep access tokens in
   memory. Use OIDC Authorization Code with PKCE and device registration.
9. Filter EventEnvelope by ownership and visibility. Internal events and model
   reasoning never cross the user event API.
10. Use optimistic project revisions for Active Version promotion. Conflicts
    create candidates instead of last-write-wins overwrites.
11. Scan memory input for secrets, invisible controls, and instruction-like
    payloads before command persistence. Core injects identity and Scope;
    clients cannot supply authority or `scope_digest`.
12. Insert Outbox records from the canonical Event Ledger in the same database
    transaction. The trigger uses caller rights so tenant RLS remains active.
13. Treat lexical and future semantic indexes as disposable, untrusted
    projections. Resolve every hit back to tenant-scoped canonical rows and
    re-check namespace, Project, Conversation, Task, Version provenance,
    expiry, tombstones, sensitivity, and scan state before model use.
14. Bind exactly one immutable Memory Snapshot ID/hash to each Task. Build and
    persist the Snapshot in the same transaction as Task binding; later memory
    writes or projection refreshes cannot replace or mutate that context.
15. Tokenize search text in Core, quote terms, and bind the resulting query as
    a SQL parameter. Never concatenate user FTS syntax. SQLite FTS5 and
    PostgreSQL `websearch_to_tsquery` remain inside dialect adapters.
16. Escape and label every selected memory source as data. Reject secret-like,
    instruction-like, bidi/control, expired, rejected, and forgotten material.
    A stale or failed projection must produce an explicit degraded Snapshot
    from bounded relational fallback, never fabricated scores or silent reuse.
17. Require `task_id` on every public retrieval request. Core resolves tenant,
    Project, Conversation, Version, generation, and the Task-bound Snapshot;
    clients cannot supply or override those fields. PostgreSQL FORCE RLS and
    local tenant predicates are both covered by same-ID isolation tests.
18. Permit only the Rust Worker's read-only static Preview server on the
    Windows host. It binds exact IPv4 loopback on an ephemeral port, serves one
    canonical managed Version, accepts only GET and HEAD, caps request-target
    size, disables directory listing and caching of HTML, and applies CSP,
    referrer, and MIME-sniffing controls. It never starts a project process,
    interpreter, package manager, or host shell.
19. Treat Runtime executor metadata as untrusted. Validate exact loopback host,
    port, URL, Preview identity, managed root, and executor handle before
    durable state advances. Recovery probes must return the exact requested
    handle; rebinding fails with `SCOPE_MISMATCH` and an interrupted Runtime.
20. Serialize lifecycle operations inside one Core instance and put a unique
    instance identity in every start/stop lease owner. A different Core
    instance cannot probe or complete that operation until its durable lease
    expires and is fenced; same-key replay cannot dispatch a second server.
21. Enforce one active Preview per tenant and Task in PostgreSQL with a partial
    unique index, while Runtime and Preview revisions use compare-and-swap.
    Runtime, Preview, Artifact, Event, and Outbox rows remain tenant-scoped and
    are covered by forced-RLS same-ID tests.
22. Keep provider credential values in the local credential adapter or Cloud
    deployment secret store. Core receives provider profiles and credential
    references only. Redact string/repr values and prohibit secrets in events,
    prompts, artifacts, HTTP errors, and logs.
23. Treat web URLs, redirects, DNS answers, media types, and bodies as
    untrusted. Require HTTPS where applicable, reject loopback/private/link-local
    destinations and credential-bearing URLs, revalidate every redirect and DNS
    result, cap bytes/time, and persist source-labelled Evidence.
24. Keep document blobs separate from Hermes Memory. Verify hash, size,
    revision, tenant, Project/Conversation visibility, and deletion state before
    parsing or retrieval. A RAG hit cannot write a Claim.
25. Treat model tool calls as candidates, never authority. Validate against the
    generated ToolDefinition schema, drop model-supplied Scope fields, and
    require the linked fenced CommandRun before adapter execution.
26. Allow Windows host actions only through the tagged action union and the
    durable prepared/completed/failed journal. Restrict URLs to HTTPS, paths to
    Core-resolved managed paths, settings to a fixed enum, and text sizes to
    bounded public values. Never expose a process or input-simulation API.
27. Make screen capture an explicit user action. Bind image bytes, dimensions,
    media type, and hash to one Turn; label OCR/vision text as untrusted and
    remove ephemeral bytes after terminal completion, cancellation, or failure.
28. Keep the Fairy Pet outside CoreClient. Accept only schema-validated public
    projections and typed scratch-chat/window/voice requests over the
    cross-window channel. Bound reply text, reject Scope and project data, and
    make approval notices open the main window instead of deciding anything.
29. Validate Outbox delivery against the shared EventEnvelope plus exact tenant
    and event identity. Give projection handlers event ID, attempt, and lease
    fence; a stale worker cannot acknowledge a newer claim.
30. Select dynamic Runtime commands only from strict Core templates. Bind the
    immutable Version archive, Project Index generation, dependency lock key,
    Scope digest, Runtime/Preview identity, and monotonic Runtime revision
    fence before dispatch. Model-provided argv, cwd, host, port, URL,
    environment, and endpoint fields are never accepted.
31. Publish dependency layers atomically from a fixed, attested Node 24/uv
    toolchain. Dependency jobs alone receive public package-registry access;
    Review and Runtime mount a completed matching layer read-only. Python
    packages install into the managed venv rather than the system interpreter.
32. Keep dynamic project processes on exact loopback and deny outbound
    `connect`, datagram-send, and `io_uring` setup syscalls with a
    supervisor-generated seccomp program inherited by all children. The
    program is passed through a private FD and cannot be changed by project
    files or model output.
33. Bind Cloud Preview capability tokens to tenant, Task, Version, Preview,
    Runtime, fence, and expiry, and store only their hashes. Require a separate
    32-byte gateway secret between API and Runtime services; strip incoming
    credentials and internal headers, reject noncanonical internal targets,
    stream within byte limits, and rewrite loopback redirects to the public
    capability origin.
34. Run browser Review only against the active Runtime's validated loopback
    endpoint. Force local browser traffic through an unreachable proxy with an
    exact loopback bypass, use an isolated profile and scrubbed environment,
    and accept only bounded PNG evidence whose dimensions match its header.
    Cloud Review additionally rechecks the full durable lease/Scope binding.
35. Treat renderer permission state as an untrusted projection. Persist profile
    and capability overrides only through a revision-fenced Core update; never
    read them from local storage, accept renderer sandbox-health claims, or
    automatically merge a conflict. Generate capability toggles and Slash
    availability from the same typed Registry metadata and effective policy.
36. Treat every Skill package as untrusted prompt material. Require a strict
    manifest and frontmatter, canonical content digest, regular files inside a
    bounded package root, and explicit provenance. Reject symlinks, traversal,
    executable hooks, script directories, oversized content, and capability
    claims that are absent from the Registry.
37. Keep Fairy Skills separate from Codex Skills. A Fairy Skill can add only a
    read-only instruction ToolDefinition and cannot resolve secrets, select an
    MCP endpoint, supply Scope identity, execute a process, mutate policy, or
    persist Project/Conversation state.
38. Treat MCP configuration, discovery, schemas, and output as untrusted.
    Endpoint, fixed argv, transport, credential references, and environment
    references are user/deployment configuration and never model arguments.
    Cloud rejects stdio and requires an exact deployment hostname allowlist.
    Remote endpoints require HTTPS and public DNS addresses; redirects,
    non-public destinations, and ambient proxy environment are disabled.
39. Import MCP tools only after bounded schema sanitization and explicit
    per-tool trust policy. Namespace names, reject collisions and reserved
    Scope/credential fields, ignore server-declared trust annotations, and
    remove accepted tools immediately on schema digest drift.
40. Execute MCP through the immutable Task Scope, effective Capability
    Manifest, Policy Matrix, Approval, leased CommandRun, cancellation, and
    Task-owned Artifact path. Use one Registry snapshot per model round and
    compare its definition digest again before dispatch and approval resume.
41. Reserve MCP lifecycle idempotency keys before mutation and persist record,
    deletion tombstone, failure, or pending outcomes without server-delete
    cascade. Retry only explicitly idempotent read/none calls once. Treat all
    mutating, response-started, duplicate-active, and crash-uncertain calls as
    `MCP_RESULT_UNCERTAIN`; never infer success or repeat them automatically.
42. Bound MCP tool count, pagination, schema depth/properties/items, text and
    structured result bytes. Accept supported text/structured blocks only,
    validate declared output schema, suppress stdio stderr/protocol frames, and
    label all result content as untrusted data rather than instructions.
43. Keep release structure executable as policy. Reject privileged Renderer
    file/process imports, open tool schemas, unowned source types, oversized or
    empty source modules, Docker sockets, host namespaces, added container
    capabilities, and host bind mounts on project execution services. Run the
    gate before tests and make its malicious fixtures fail for each rule.

## Environment verification

Static Preview is not a general execution sandbox. Dynamic project execution
is exposed only when a dedicated WSL2 or cloud OCI executor is configured and
healthy; otherwise the capability is unavailable and never falls back to
Windows process execution. The bundled Compose Outbox Worker is not that
executor.

The default verification run reports WSL as skipped. `-RequireWslSandbox`
requires a real `FairySandbox` WSL2 configuration/toolchain attestation and
real static plus dynamic Preview lifecycle tests. PostgreSQL 18, RLS, S3,
cloud Runtime recovery, Chromium Review, and capability proxy claims are made
only when the Docker integration profile actually runs; generated DDL,
mocked supervisors, or SQLite tests are not reported as real environment
execution.

## Security error contract

Security failures use stable codes: PATH_OUT_OF_SCOPE, PATH_IDENTITY_CHANGED,
SCOPE_MISMATCH, APPROVAL_REQUIRED, SANDBOX_UNAVAILABLE, VERSION_CONFLICT,
IDEMPOTENCY_CONFLICT, SECRET_EGRESS_BLOCKED, CAPABILITY_NOT_AVAILABLE,
WORKER_INTERRUPTED, MEMORY_SCOPE_VIOLATION, MEMORY_CONFLICT,
MEMORY_INJECTION_BLOCKED, MEMORY_SECRET_BLOCKED, MEMORY_PROJECTION_STALE,
MEMORY_SNAPSHOT_TOO_LARGE, MEMORY_FORGOTTEN, DOCUMENT_PROJECTION_STALE, and
DOCUMENT_INTEGRITY_FAILED, MCP_CAPABILITY_MISSING,
MCP_CREDENTIAL_UNAVAILABLE, MCP_DESTINATION_BLOCKED, MCP_OUTPUT_INVALID,
MCP_OUTPUT_UNSUPPORTED,
MCP_PROTOCOL_MISMATCH, MCP_RESULT_UNCERTAIN, MCP_SCHEMA_CHANGED,
MCP_SCHEMA_INVALID, MCP_TOOL_ERROR, MCP_TRANSPORT_INTERRUPTED,
MCP_TRANSPORT_NOT_ALLOWED, and MCP_UNAVAILABLE. Cloud maps every public code
to an explicit HTTP status rather than relying on a generic fallback.
