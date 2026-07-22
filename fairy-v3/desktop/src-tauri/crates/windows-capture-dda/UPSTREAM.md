# Upstream provenance

This crate is a GPU-only extraction and adaptation of the DXGI Desktop
Duplication implementation in `windows-capture` 2.0.0:

- Repository: https://github.com/NiiightmareXD/windows-capture
- Release: 2.0.0
- License: MIT
- Relevant upstream module: `src/dxgi_duplication_api.rs`

Fairy removes CPU frame mapping, image encoding, recording and Windows Graphics
Capture. It adds explicit adapter/output binding and caller-owned D3D11 device
support so capture and DirectComposition rendering use the same GPU device.

The adapter/output/device injection shape was cross-checked against
`rhinostream/win_desktop_duplication` 0.10.11 (MIT OR Apache-2.0). No source from
that crate is included because it depends on an incompatible `windows` crate
version.
