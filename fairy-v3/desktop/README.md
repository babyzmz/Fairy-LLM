# Fairy Desktop

The desktop package contains the React renderer, the Tauri host, the local
Core bridge, and the Rust local worker. The renderer has one privileged path:
the `core_rpc` Tauri command. It cannot invoke the worker or host processes
directly.

The renderer uses one strongly typed `CoreClient`. `TauriCoreTransport` sends
the public method map over private JSON-RPC; `CloudCoreTransport` maps the same
methods to authenticated REST and resumable SSE. Network event envelopes are
validated with Zod before reaching React.

The main workspace is backed only by durable Core collections and user-visible
Ledger events. It presents Task Timeline plus Preview, explicit approval and
version decisions, loading/offline/interrupted/conflict states, and a lazy
developer drawer. The Preview iframe accepts only validated loopback HTTP or
cloud HTTPS and does not grant same-origin access.

The Extensions panel lists verified Skill provenance and manages MCP server
trust. MCP discovery requires a durable Task; schema acceptance reviews every
tool's effect, risk, approval, profiles, idempotency, and enabled state. The
renderer submits only credential references and never receives secret values.

The Local Worker crate is split by boundary: `protocol.rs` owns the fixed
JSON-RPC method map, `workspace.rs` owns managed Git and Changeset operations,
`preview.rs` owns the read-only loopback server, and `error.rs` owns stable
worker failures. The crate root only exports the supported API.

Run the root `scripts/generate-contracts.ps1` command after a Core or cloud API
contract change. `npm run generate:contracts` regenerates only the desktop
declaration from the existing OpenAPI snapshot.

```powershell
npm install
npm test
npm run e2e
npm run build
npm run tauri -- build --debug --no-bundle
powershell -NoProfile -ExecutionPolicy Bypass -File ..\scripts\start-desktop.ps1
```

Use the repository launcher for desktop development. It keeps Vite's strict
filesystem policy enabled while handling Windows repository paths that contain
non-ASCII characters or a literal `~`. A plain browser receives a dedicated
host-boundary screen; only Tauri or the explicit Playwright fixture mounts a
`CoreClient`.

The development Tauri process launches Python Core over line-oriented
JSON-RPC. Core then launches the same `fairy.exe` with `--local-worker` for
scoped Git workspace operations. Neither boundary invokes a host shell.
The Local Worker may invoke Git with fixed arguments for managed repository
operations; it never accepts a generic command or shell string. Static Preview
serves files only and never invokes `Command`.

The complete cross-package acceptance record, including the exact distinction
between production-browser evidence and unavailable Docker/WSL gates, is in
`../docs/completion-audit.md`.
