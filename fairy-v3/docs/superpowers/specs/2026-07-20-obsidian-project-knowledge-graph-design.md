# Fairy V3 Obsidian Project Knowledge Graph Design

- Status: approved for implementation
- Date: 2026-07-20
- Scope: project knowledge, Obsidian interoperability, graph projection, Harness context, and Hermes proposals

## Intent

Fairy integrates the official Obsidian desktop application as an optional,
user-editable knowledge companion for project conversations. The primary product
surface remains Fairy: the right Workspace Inspector exposes an `Obsidian` tab
with Overview, Notes, Links, Graph, and Sync views. Obsidian provides a second,
native editing and graph surface for the Markdown subset of project knowledge.

The integration must improve multi-file project navigation and associative recall
without turning a Vault into Fairy's database. Project source files remain in the
managed Workspace, Hermes remains the sole memory authority, and every Assistant
turn consumes immutable snapshots rather than a live Vault.

## Product model

Each Fairy Project may bind one project knowledge collection inside a device-local
Obsidian Vault. A collection contains user-selected read-only folders and one
Fairy-managed subtree:

```text
Fairy/
  Notes/
  Projects/<stable-project-id>/
    Project.md
    Conversations/
    Files/
    Artifacts/
    Games/
  Memory/
```

Fairy never mirrors Workspace contents into this tree as a second editable source.
Instead it writes managed Markdown index notes for source files, symbols, Tasks,
Artifacts, and Conversations. Stable WikiLinks between these notes make the
project's editable subset visible in Obsidian Graph. Fairy's own graph additionally
contains native entities that Obsidian cannot represent directly.

## Authority boundaries

1. Core owns Projects, Conversations, Tasks, Workspace Versions, Commands, Ledger,
   Harness manifests, canonical Knowledge revisions, and Hermes memory.
2. Workspace owns executable project files. Obsidian cannot mutate Workspace files.
3. User-selected ordinary Vault folders are read-only knowledge sources.
4. Fairy may write only beneath the configured managed subtree.
5. A Vault edit can create a Knowledge revision or Memory proposal, never a Hermes
   Claim, Task transition, approval, Command, or Workspace mutation.
6. The Renderer, Pet, model, MCP server, and Obsidian process cannot bypass the
   Command Bus.
7. Device paths are local secrets. Contracts and events expose source IDs and
   relative display paths only.

## Project knowledge graph

The graph is a rebuildable projection over canonical Fairy entities and immutable
Knowledge revisions. It is not a graph database and is never authoritative.

Node kinds are `project`, `conversation`, `task`, `workspace_file`, `symbol`,
`artifact`, `knowledge_note`, `memory_claim`, and `external_reference`. Edge kinds
are `contains`, `links_to`, `backlinks`, `mentions`, `imports`, `depends_on`,
`generated_by`, `discussed_in`, `supports`, `contradicts`, and `supersedes`.

Edges carry provenance, source revision, confidence, and visibility. Deterministic
edges come from paths, Markdown links, frontmatter, imports, Task/Artifact ownership,
and accepted Hermes sources. Semantic suggestions are stored separately and appear
as dashed suggestions until the user accepts them; a model cannot silently create a
canonical relation.

The Fairy graph supports filtering by node and edge kind, text search, local-neighbor
expansion, depth one to three, orphan display, backlinks, and opening the owning
file, conversation, Task, Artifact, or Obsidian note. It does not expose absolute
paths or raw database identifiers in ordinary mode.

## Knowledge synchronization

The first release uses an allowlisted Obsidian CLI adapter for open/create/append/
move operations and a Core-owned, read-only scanner for indexing. It does not depend
on a community REST plugin or MCP server. Forbidden CLI surfaces include `eval`,
generic `command`, plugin management, arbitrary shell, and paths outside the managed
subtree.

The scanner accepts Markdown, Canvas JSON, and bounded attachment metadata. Existing
document parsers handle supported attachment content. It excludes `.obsidian`,
`.trash`, hidden folders, Git metadata, caches, symlinks, junctions, reparse points,
UNC/device paths, ADS paths, and files outside the canonical Vault root.

An item is identified by source, normalized relative path, and source hash. Every
change creates an immutable Knowledge revision. Rename preserves item identity;
delete appends a tombstone. Sync runs are leased, cursor-based, idempotent, bounded,
and resumable after Core restart.

Obsidian CLI absence disables managed writes but not safe read-only scanning. Pending
writes remain explicit and retryable. Fairy never falls back to direct writes for a
failed CLI operation.

## Bidirectional semantics

Confirmed Hermes Claims may be exported as managed projection notes containing a
stable claim ID, revision, origin digest, and content hash. User edits create a
Memory proposal with a safe diff. Acceptance appends a Hermes revision through the
Command Bus; rejection restores no file automatically. Deleting a projection note
creates a forget proposal and never directly deletes memory.

Origin IDs and content digests suppress write loops. Concurrent Core and Vault edits
produce a conflict with both versions preserved. No last-writer-wins merge is used.
Accepted GameMemoryDigest records may become user-confirmed observations and managed
game notes, but raw audio, frames, captions, provider items, and realtime context are
never persisted or exported.

## Harness context

Each Turn binds one immutable HarnessContextManifest containing Scope digest,
Workspace Version, Memory Snapshot ID/hash, Knowledge Snapshot ID/hash, Tool Registry
generation, Skill digests, MCP capability snapshot, model selection, source
watermarks, and budgets. Assistant context is assembled only from this manifest.

The model receives bounded `knowledge.search`, `knowledge.read`, `knowledge.links`,
`knowledge.open_in_obsidian`, `memory.search`, and `memory.suggest` tools. Results
include safe provenance and snapshot identity. A live Vault change cannot alter an
already-running Turn.

## Desktop surface

Workspace Inspector navigation becomes `Preview / Files / Outputs / Obsidian`.
The Obsidian surface contains:

- Overview: source health, snapshot, linked project, counts, and recent changes.
- Notes: searchable project notes and managed indexes with safe previews.
- Links: outgoing links, backlinks, unlinked mentions, and suggested relations.
- Graph: the complete Fairy project graph with filters and entity routing.
- Sync: last run, pending writes, conflicts, failures, and manual sync controls.

Knowledge & Privacy settings owns the connection wizard, source folders, managed
subtree, read-only/bidirectional mode, Memory policy, proposals, and diagnostics.
No personal Vault is touched until the user explicitly completes the wizard.

## Threat model

Vault content is untrusted model input. The pipeline enforces file/count/size limits,
normalization, prompt-injection scanning, secret classification, binary type checks,
and Scope filtering before snapshot selection. Markdown HTML is never executed.
Canvas references and links cannot escape the source root. External URLs remain
references and are not fetched during sync.

All mutations are revision-fenced Commands with durable public events. The settings
window receives only knowledge, proposal, and connector RPCs. Cloud sync omits local
paths and is off for normalized Vault content by default.

## Delivery order

1. Core-owned Memory settings, candidate proposal flow, and governed memory tools.
2. Knowledge aggregates, repositories, SQLite/PostgreSQL migrations, and contracts.
3. Obsidian detection, allowlisted CLI adapter, safe scanner, and resumable sync.
4. Graph projection, snapshots, and HarnessContextManifest binding.
5. Hermes projections, proposal conflicts, and GameMemory bridge.
6. Workspace Inspector Obsidian surface and settings wizard.
7. Contract generation, cloud parity, real test Vault smoke, and final cleanup.

## Acceptance gates

- A project with multiple files, notes, conversations, Tasks, and Artifacts produces
  a navigable graph in Fairy and a linked managed-note graph in Obsidian.
- Vault edits appear only in newly created snapshots; active Turns remain unchanged.
- No Vault action can mutate a Workspace or Hermes Claim without a fenced Command and
  explicit policy or user confirmation.
- Connector failure never blocks chat, project execution, Preview, Files, or Pet.
- All writes remain inside the configured managed subtree and survive retry without
  duplicate notes, revisions, proposals, or graph edges.

## References

- <https://obsidian.md/>
- <https://obsidian.md/help/cli>
- <https://docs.obsidian.md/Plugins/Vault>
- <https://docs.obsidian.md/Plugins/Events>

