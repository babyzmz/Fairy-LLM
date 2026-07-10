# Fairy V3

Fairy V3 is a new Windows-first desktop AI application. It is developed in
parallel with the legacy Fairy implementation and does not import legacy
runtime modules or migrate legacy databases.

The product has two first-class workspaces:

- scratch conversations for general assistant tasks;
- project conversations for scoped, versioned project execution.

The architecture is project-first, task-driven, preview-first, and
core-owned. Every side effect crosses the command bus and is recorded in the
durable ledger before execution.

## Repository layout

```text
fairy-v3/
|- core/       Python domain, application services, and transports
|- desktop/    React/Tauri shell and the Rust local worker
|- contracts/  Generated OpenAPI, JSON Schema, TypeScript, and Rust contracts
|- cloud/      Cloud API/worker composition and deployment files
|- tests/      Cross-transport, security, recovery, and end-to-end tests
`- docs/       Architecture, threat model, ADRs, and operating documentation
```

The implementation is intentionally independent. Legacy character assets,
voice assets, doctrine, and black-box behavior may be used as references only.

## Current milestone

The local vertical slice is executable: Tauri supervises Python Core, Core
dispatches scoped workspace operations to the Rust worker, and the durable
ledger covers import, worktree creation, Changeset approval, review,
checkpoint, accept, discard, capabilities, and resumable events. Cloud and
assistant capability packages remain separate milestones.
