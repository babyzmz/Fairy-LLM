# ADR 0012: Close the Desktop Project Workflow Through Core

- Status: Accepted
- Date: 2026-07-12

## Context

The desktop could display imported projects, Tasks, approvals, and previews, but
several gaps prevented a user from completing the governed project workflow.
The Renderer could not select a native folder, long timelines could hide a
pending approval, equivalent Windows path spellings could fail scope checks,
and a failed candidate version could not be explicitly discarded.

## Decision

The Tauri main window exposes one restricted folder-selection command. The
dialog returns a path to the Renderer, but project import remains a Core command;
the host does not copy files or mutate project state. Dialog plugin permissions
are not granted directly to web content.

Pending approvals remain visible at the bottom of the Task timeline. Approval,
preview, review, acceptance, and discard continue to use generated CoreClient
contracts and durable Core state. Terminal Tasks expose status rather than stale
decision controls.

Runtime scope checks compare normalized Windows path identities. Extended path
prefixes such as `\\?\C:\...` and ordinary drive paths are treated as equivalent
after normalization, while the Rust worker remains responsible for canonical
containment and reparse-point safety.

A failed Task with a candidate version may transition explicitly to `rejected`.
This discards the candidate workspace through Core without promoting it to the
Active Version. Failed candidates can never be accepted.

## Consequences

- A user can create or import a project and complete the full Task-to-Version
  workflow without entering a path manually.
- Approval visibility no longer depends on timeline length.
- Canonical Windows paths returned by the worker do not cause false
  `SCOPE_MISMATCH` failures.
- Interrupted or failed candidate work has a durable, auditable cleanup path.
- Folder selection does not expand Renderer filesystem authority.
