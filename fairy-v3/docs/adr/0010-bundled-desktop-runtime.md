# ADR 0010: Bundle the Desktop Core and Git Runtime

- Status: Accepted
- Date: 2026-07-12

## Context

The development desktop launched Core from repository virtual environments and
resolved Git from `PATH`. That proved the process boundaries but did not produce
a standalone Windows application. A release could fail on a clean machine or
silently run an unrelated Python or Git installation.

## Decision

Release builds package the composed `fairy_capabilities.stdio` entry point as a
single `fairy-core` sidecar. The sidecar is built from the locked Python 3.13
environment, smoke-tested against the Core health contract, and declared as a
Tauri external binary. Release LaunchSpec never falls back to system Python or
repository source paths.

The desktop also packages Git for Windows MinGit 2.55.0.2. Its official archive
is pinned by SHA-256 and verified before extraction. Tauri resolves the bundled
resource path and passes it through Core to the Rust Local Worker as
`FAIRY_GIT_PROGRAM`; the model and Renderer cannot supply or change this path.

CoreBridge performs a startup health handshake and rejects a sidecar whose
service identity or `core-service-v1` protocol does not match the desktop.
Development remains source-based and explicit so local iteration does not hide
packaging errors.

## Consequences

- A release runs without Python, uv, Git, or the source repository installed.
- Sidecar and MinGit build artifacts remain ignored; the signed installer owns
  their distribution.
- Updating Python, PyInstaller, or MinGit requires lock/hash review and a fresh
  release composition test.
- The release gate must exercise Project creation through the bundled Core,
  Local Worker, and MinGit chain rather than only probing each executable.
