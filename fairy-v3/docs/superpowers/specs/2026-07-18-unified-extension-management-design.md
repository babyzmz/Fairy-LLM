# Fairy Unified Skills and MCP Management Design

- Status: accepted by recommended-default instruction
- Date: 2026-07-18
- Related: ADR 0009, Workspace Loop And Extensions Acceptance

## Goal

Make Skills and MCP feel like two extension types inside one governed product
surface. Both expose `Installed` and `Store` views and one consistent add flow,
while preserving their different trust models.

## Chosen Approach

Fairy owns a typed extension catalog and type-specific installers. The catalog is
not rendered from React constants. Core returns store metadata, installation
state, compatibility, source trust, required setup, and an immutable install
recipe.

Alternatives rejected:

- Linking external Skill directories in place. Links recreate Windows ACL,
  junction, lifetime, and mutation failures and let files change after review.
- Installing arbitrary MCP Registry entries without curation. Registry presence
  is discovery metadata, not a Fairy security endorsement.
- Keeping separate hard-coded Skills and MCP pages. This has already caused
  Context7-only React branching and missing MCP store behavior.

The official MCP Registry remains a discovery source, but the first Fairy store
contains a reviewed allowlist with pinned recipes. Playwright MCP, Context7, and
GitHub MCP are the initial presets. Playwright is the no-credential functional
test preset; Context7 is the remote documentation preset; GitHub remains marked
as requiring credentials and explicit setup.

References:

- <https://registry.modelcontextprotocol.io/docs>
- <https://modelcontextprotocol.io/registry/quickstart>
- <https://github.com/microsoft/playwright-mcp>
- <https://github.com/github/github-mcp-server>

## User Experience

`Settings -> Skills / MCP` keeps the top-level `Skills` and `MCP` tabs. Each type
then exposes the same secondary navigation:

- `Installed`: health, version, provenance, enable state, update, remove, and
  type-specific review state.
- `Store`: searchable curated entries with publisher, trust badge, modality,
  setup requirements, install state, and details.
- `Add`: a command menu, not a permanent third tab.

Skill Add actions:

- Import Git URL
- Import ZIP
- Import local folder
- Create Skill

MCP Add actions:

- Add local stdio server
- Add Streamable HTTP server
- Import JSON configuration

The Skill creator is a constrained form for name, description, instructions,
input fields, capabilities, compatible MCP IDs, publisher, license, and version.
It previews the generated manifest and installs through the same staging and
validation path as an external package. It is not an unrestricted filesystem
editor and cannot add executable hooks.

## Skill Import Boundary

Every external source becomes a managed immutable copy:

1. Resolve the selected source into a private staging directory.
2. Reject traversal, absolute archive paths, symlinks, junctions, reparse points,
   executable files, oversized files, excessive file counts, and nested package
   roots.
3. Require `SKILL.md`. Accept an existing `fairy-skill.json` only if valid;
   otherwise generate it from user-confirmed metadata.
4. Compute the package digest after normalization.
5. Present provenance, capabilities, compatible MCP IDs, and warnings.
6. Install atomically into the Fairy Skills root and register only after the
   durable state update succeeds.

Source paths and Git credentials never enter model context. Imported packages do
not retain a live filesystem link. Updates are explicit re-import operations.

## MCP Store Boundary

An MCP preset contains connection metadata, source provenance, version policy,
credential requirements, and a pinned recipe. Installing a preset creates or
updates an untrusted disabled server record. It does not import tools.

The user then runs `Discover`, reviews the returned schema and per-tool side
effect/risk/approval policy, and accepts it. Only accepted enabled tools enter the
single Tool Registry.

Local stdio recipes use an exact package version and fixed argv. Remote endpoints
must be HTTPS except loopback. Store recipes cannot inject credentials or Scope.
The Settings window receives only extension-management RPC methods.

## Contracts

Extend `ExtensionCatalogEntryModel` with typed installation metadata:

- `kind: skill | mcp_preset`
- `source_kind: curated | git | archive | local | created`
- `trust: verified_publisher | curated | external | local`
- `install_state: not_installed | installed | update_available | unavailable`
- `requirements`, `tags`, `setup_fields`, and safe `source_url`

Add:

- `skills.import.inspect`
- `skills.import.install`
- `skills.create`
- `mcp.presets.install`

Inspection returns a short-lived opaque token bound to the normalized staged
package digest. Install consumes that token once. React cannot provide a target
path, digest, capability decision, MCP command, or preset endpoint.

## Failure and Recovery

- Failed imports leave no installed package and remove staging data.
- Interrupted imports are cleaned on startup and never registered.
- ACL failures quarantine only the affected package and never prevent Core boot.
- Store refresh uses last-known-good metadata and shows stale/unavailable state.
- MCP process or discovery failure leaves the server disabled and retryable.
- Duplicate install requests are idempotent by source digest or preset revision.

## Verification

- Core filesystem tests cover ZIP traversal, reparse points, executable content,
  quotas, digest changes, interrupted staging, ACL errors, and atomic rollback.
- Contract tests cover local JSON-RPC and cloud REST parity.
- Settings tests cover both extension types, both secondary tabs, all Add flows,
  keyboard focus, narrow windows, and disabled explanations.
- A real local probe installs GSAP Core and GSAP ScrollTrigger through the user
  store UI, then installs Playwright MCP, discovers its tools, accepts a bounded
  policy, restarts Core, and verifies all three remain installed.
