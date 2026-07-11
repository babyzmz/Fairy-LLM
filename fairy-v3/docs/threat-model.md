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
28. Keep Presence and Guide outside CoreClient. Send only fixed projections of
    public events over the cross-window channel; reject arbitrary text, Scope,
    project data, approvals, and execution requests.
29. Validate Outbox delivery against the shared EventEnvelope plus exact tenant
    and event identity. Give projection handlers event ID, attempt, and lease
    fence; a stale worker cannot acknowledge a newer claim.

## Environment verification

Static Preview is not a general execution sandbox. Dynamic project execution
is exposed only when a dedicated WSL2 or cloud OCI executor is configured and
healthy; otherwise the capability is unavailable and never falls back to
Windows process execution. The bundled Compose Outbox Worker is not that
executor.

The default verification run reports WSL as skipped. `-RequireWslSandbox`
requires a real `FairySandbox` WSL2 attestation and the real Rust static
Preview lifecycle test. PostgreSQL 18, RLS, recovery, and S3 claims are made
only when the Docker integration profile actually runs; generated DDL or
SQLite tests are not reported as PostgreSQL execution.

## Security error contract

Security failures use stable codes: PATH_OUT_OF_SCOPE, PATH_IDENTITY_CHANGED,
SCOPE_MISMATCH, APPROVAL_REQUIRED, SANDBOX_UNAVAILABLE, VERSION_CONFLICT,
IDEMPOTENCY_CONFLICT, SECRET_EGRESS_BLOCKED, CAPABILITY_NOT_AVAILABLE,
WORKER_INTERRUPTED, MEMORY_SCOPE_VIOLATION, MEMORY_CONFLICT,
MEMORY_INJECTION_BLOCKED, MEMORY_SECRET_BLOCKED, MEMORY_PROJECTION_STALE,
MEMORY_SNAPSHOT_TOO_LARGE, MEMORY_FORGOTTEN, DOCUMENT_PROJECTION_STALE, and
DOCUMENT_INTEGRITY_FAILED. Cloud maps every public code to an explicit HTTP
status rather than relying on a generic fallback.
