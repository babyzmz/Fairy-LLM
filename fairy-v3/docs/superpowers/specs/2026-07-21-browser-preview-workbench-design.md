# Fairy Browser and Preview Workbench Design

## Decision

Fairy keeps Preview as the primary right-side workspace. Runtime, controlled Browser,
responsive inspection, and developer diagnostics are inner Preview modes rather than new
top-level application destinations.

Browser automation is a Core capability. React, agents, Skills, MCP servers, and remote pages
never receive a CDP endpoint or direct process access.

## Architecture

- `BrowserService` owns scoped Browser Sessions, tabs, recovery metadata, and public RPC methods.
- A supervised Node 24 Playwright Worker owns Edge contexts, pages, accessibility snapshots, and
  screenshots. Core closes the worker and its browser contexts with the rest of the local service.
- The local persistent profile is Fairy-specific. Fairy never opens the user's default Edge or
  Chrome profile.
- Model-visible browser operations are generated from the central Tool Registry and pass through
  existing policy, approval, CommandRun, and TurnTrace behavior.
- Arbitrary Playwright programs are not run in the host worker. A future code-job adapter must use
  the credential-free WSL2 or OCI sandbox boundary.

## Security and Privacy

- Browser RPC methods are `local_only`; Cloud Transport must reject them before HTTP dispatch.
- URLs are limited to HTTP(S), reject embedded credentials, and block private, link-local,
  multicast, and unspecified destinations. Loopback remains available for scoped Runtime Preview.
- Page content and accessibility snapshots are untrusted tool data, never instructions.
- Screenshots and complete page snapshots are transient. Durable state contains only scoped
  session metadata and is retained for seven days.
- Read operations do not require approval. Page mutation uses profile policy; irreversible account,
  upload, download, and payment operations require later typed actions with explicit approval.

## Interaction

- Preview modes are Runtime, Browser, Responsive, and Developer Diagnostics.
- Browser provides navigation, address entry, refresh, start/stop, page image interaction, and
  Runtime handoff. Every action carries a page revision so stale interactions fail closed.
- The inspector width is drag-resizable from 360 pixels to 75 percent of the viewport and stored
  locally. Narrow layouts stack the inspector below the primary workspace.
- Runtime Preview remains independent. A Browser failure cannot stop Runtime, Core, chat, or Pet.

## Recovery

- Active sessions become interrupted after a Core restart and require explicit resume.
- Completed and failed session metadata expires after seven days.
- Browser writes are never automatically replayed after interruption.
- Closing Fairy closes worker stdin, browser contexts, and the worker process tree.

## Acceptance

- Local Core can start and stop a Playwright-backed Edge context without leaked processes.
- Browser methods are absent from cloud-supported transport methods.
- Preview can open the current Runtime in Browser and display a current page screenshot.
- Switching Conversation or Task does not expose another scope's Browser Session.
- TypeScript, Vitest, Core transport tests, Ruff, and a real Edge smoke test pass.
