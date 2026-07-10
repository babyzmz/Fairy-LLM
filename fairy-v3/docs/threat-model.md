# Fairy V3 Threat Model

## Protected assets

- user source code and documents;
- secrets, credentials, tokens, and private prompts;
- project, conversation, task, version, and artifact ownership;
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
7. Run cloud workers as non-root with read-only base filesystems, resource
   limits, scoped mounts, and controlled egress.
8. Store refresh credentials in the Tauri secure store; keep access tokens in
   memory. Use OIDC Authorization Code with PKCE and device registration.
9. Filter EventEnvelope by ownership and visibility. Internal events and model
   reasoning never cross the user event API.
10. Use optimistic project revisions for Active Version promotion. Conflicts
    create candidates instead of last-write-wins overwrites.

## Security error contract

Security failures use stable codes: PATH_OUT_OF_SCOPE, SCOPE_MISMATCH,
APPROVAL_REQUIRED, SANDBOX_UNAVAILABLE, VERSION_CONFLICT,
SECRET_EGRESS_BLOCKED, CAPABILITY_NOT_AVAILABLE, and WORKER_INTERRUPTED.
