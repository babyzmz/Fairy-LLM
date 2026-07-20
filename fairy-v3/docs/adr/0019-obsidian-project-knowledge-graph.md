# ADR 0019: Obsidian Project Knowledge Graph

## Status

Accepted for Fairy V3 Windows-first development.

## Context

Project conversations need durable multi-file knowledge, associative navigation,
backlinks, and a graph view. Obsidian provides a strong local Markdown editing and
graph experience, but its Vault cannot safely become Fairy's Task, Workspace, or
memory database. Obsidian's native graph also cannot directly represent all Fairy
entities such as Workspace files, symbols, Tasks, Artifacts, and Hermes Claims.

## Decision

Fairy integrates the official Obsidian desktop application as an optional project
knowledge companion. Fairy Core remains authoritative and stores immutable Knowledge
revisions and task-bound snapshots. Workspace remains authoritative for executable
files, and Hermes remains authoritative for memory.

The right Workspace Inspector exposes an Obsidian surface with Overview, Notes,
Links, Graph, and Sync views. Fairy maintains a rebuildable project graph spanning
native project entities and knowledge revisions. A managed Markdown projection lets
the editable subset participate in Obsidian Graph without duplicating source files.

Ordinary selected Vault folders are read-only. Fairy writes only beneath a configured
managed subtree through an allowlisted official CLI adapter. A safe native scanner
indexes allowed content. Vault edits can create Knowledge revisions and Memory
proposals, but cannot directly mutate Hermes Claims, Workspace Versions, Commands,
approvals, or Tasks.

Every Assistant Turn binds an immutable HarnessContextManifest containing Memory and
Knowledge Snapshot hashes plus tool, Skill, MCP, model, Workspace, Scope, and budget
identities. Live Vault changes cannot alter an active Turn.

## Consequences

- Users get a full project graph in Fairy and a compatible note graph in Obsidian.
- Obsidian remains optional; connector failure cannot take Core offline.
- The graph is a rebuildable relational projection rather than a new graph database.
- Bidirectional memory edits require proposals and explicit confirmation.
- The connector must maintain strict path, command, provenance, and conflict guards.
- A dedicated test Vault is required before any personal Vault can be connected.
