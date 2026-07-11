# FairySandbox

FairySandbox is the only local general-purpose execution boundary exposed by Fairy V3.
The model submits structured `argv`; Core injects the immutable Task, Version, Scope digest,
workspace generation, lease fence, network policy, and a bounded managed-workspace archive.
No Windows host shell or mounted Windows path is available inside the distribution.

## Install

Use a trusted, pinned Linux rootfs tarball and its SHA-256 digest:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File sandbox/wsl/install.ps1 `
  -RootfsPath C:\path\to\ubuntu-rootfs.tar `
  -RootfsSha256 <64-character-sha256>
```

The installer imports the distro as WSL 2, installs Python and bubblewrap, creates the
non-root `fairy` account, writes `wsl.conf`, and installs the Runner through stdin. It
does not unregister or overwrite an existing `FairySandbox` distribution.

## Isolation Contract

- `/etc/wsl.conf` disables automount, `fstab` mounts, Windows interop, and Windows PATH.
- The adapter invokes only the fixed `FairySandbox` distro, `fairy` user, and Runner path.
- ZIP input is size bounded and rejects traversal, duplicate names, symlinks, devices,
  encrypted entries, unsupported compression, generation rollback, and hash conflicts.
- Bubblewrap exposes a private process/IPC/UTS namespace, a copied Task workspace, a
  temporary filesystem, and no network unless Core's scratch Scope explicitly allows it.
- Output is bounded, process groups are terminated on timeout/cancellation/flood, and
  returned bytes are bound to SHA-256 hashes.

## Verify

The normal release gate tests simulated adapters but reports real WSL execution as skipped.
Require the installed Sandbox and a real structured execution with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-all.ps1 `
  -SkipDocker -RequireWslSandbox
```

This gate must not be reported as passed when WSL is unavailable.
