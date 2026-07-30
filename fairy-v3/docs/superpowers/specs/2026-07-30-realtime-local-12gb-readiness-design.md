# Realtime Local Beta 12 GB Readiness Design

**Date:** 2026-07-30
**Status:** Approved
**Scope:** Fairy V3 Windows Desktop Local Realtime Beta

## Context

Fairy currently rejects an NVIDIA adapter unless Windows reports at least exactly
16 GiB of dedicated video memory. This has two defects:

1. A nominal 16 GB adapter can be reported as roughly 15.7 GiB and is rejected.
2. The dynamic budget adds the model peak, a renderer reserve, and a profile
   reserve. With the pinned MiniCPM-o 4.5 Q4 manifest this makes the Focus
   requirement about 14.3 GiB, so changing only the static label to 12 GB would
   not make a 12 GB adapter usable.

The pinned Fairy model is `MiniCPM-o-4_5-Q4_K_M.gguf` with a manifest-predicted
peak of 9,500 MiB. OpenBMB documents approximately 10 GB of GPU memory for the
GGUF deployment and describes 12 GB GPU memory as a supported realtime speech
class. Fairy should therefore treat 12 GB as the minimum supported hardware
class while retaining runtime resource and self-test gates.

Primary upstream references:

- <https://github.com/OpenBMB/MiniCPM-V>
- <https://huggingface.co/openbmb/MiniCPM-o-4_5>
- <https://arxiv.org/abs/2604.27393>

## Goals

- Admit nominal 12 GB NVIDIA adapters to Local Beta capability evaluation.
- Ensure nominal 16 GB adapters reported below exactly 16 GiB are not rejected.
- Make the dynamic budget achievable on an otherwise idle 12 GB adapter.
- Keep fail-closed CUDA, driver, adapter, model verification, runtime self-test,
  disk, and live free-budget checks.
- Distinguish unsupported hardware from temporarily insufficient free VRAM.
- Keep the existing backend selection and fallback behavior unchanged.

## Non-goals

- Supporting adapters marketed below 12 GB.
- Guaranteeing that every workload fits whenever the adapter has 12 GB.
- Relaxing model integrity, runtime quarantine, or self-test requirements.
- Changing the pinned model, quantization, runtime, download lifecycle, or
  Realtime protocol.
- Prewarming the Voice Worker or local omni runtime during normal startup.

## Decision

### Static hardware class

Use `12_000_000_000` bytes as the minimum dedicated VRAM value and present this
as “12 GB” in product copy.

This decimal threshold matches how GPU capacities are marketed and avoids
requiring Windows to report an exact binary 12 GiB allocation. An adapter below
this threshold receives the stable reason `vram_below_12gb`.

Static eligibility answers only whether the adapter belongs to the supported
hardware class. It does not promise that the model can start under the current
desktop or game workload.

### Dynamic live budget

Replace the additive renderer and activity-profile reserves with one 512 MiB
runtime headroom:

```text
available budget = Windows local-memory budget - current local-memory usage
required budget  = verified runtime/model peak + 512 MiB headroom
```

The pinned manifest fallback therefore requires 10,012 MiB rather than
14,620–16,668 MiB. A successful runtime self-test may continue to provide a
measured peak in place of the manifest fallback.

The activity profile remains part of the readiness report and runtime request,
but it does not add a second synthetic VRAM reserve. Actual renderer, desktop,
and game allocations already reduce the live available budget reported by
Windows. If those allocations leave insufficient capacity, the adapter remains
statically supported and receives the transient reason
`insufficient_free_vram`.

### Runtime verification

Model verification and the native runtime self-test remain mandatory. The
self-test is the final evidence that the current runtime/model/driver
combination can initialize correctly. Lowering the hardware class does not turn
runtime failure into eligibility.

### UI semantics

Settings and diagnostics use the following distinction:

- `vram_below_12gb`: unsupported hardware; Local Beta needs a 12 GB NVIDIA GPU.
- `insufficient_free_vram`: supported hardware is currently busy; close
  GPU-heavy applications or switch away from a heavy game before retrying.

The generic backend error code `LOCAL_VRAM_INSUFFICIENT` remains stable for the
static failure. No credentials, model paths, or device-local details are added
to telemetry.

## Compatibility and schema

The Rust enum and TypeScript reason union change from `vram_below16gb` to
`vram_below12gb`. Realtime readiness is fetched from Core and is not persisted
as durable user data, so no database migration is required. All producers,
consumers, fixtures, and documentation must change in the same commit.

The public readiness report schema version remains unchanged because its shape
does not change; only one enumerated value is corrected before Local Beta
release.

## Testing

Rust coverage must prove:

- exactly 12,000,000,000 reported bytes passes the static VRAM gate;
- a value below 12,000,000,000 bytes fails with `VramBelow12gb`;
- a roughly 15.7 GiB nominal 16 GB report passes;
- the pinned 9,500 MiB peak requires 10,012 MiB of live budget;
- an idle 12 GB fixture can be eligible after all other gates pass;
- the same adapter becomes transiently unavailable when live free budget falls
  below the unified requirement;
- Focus, Auto, and Game no longer acquire duplicate synthetic reserves.

Desktop tests must update readiness reason fixtures and verify the revised
unsupported and temporarily-busy copy.

Documentation and acceptance searches must leave no active statement that
Local Beta requires 16 GiB. Historical implementation plans may be annotated
only if they are treated as immutable execution records.

## Validation

Run targeted Rust and desktop tests first, followed by:

1. `npx tsc --noEmit`
2. the affected Vitest suites
3. `cargo test` for the affected Tauri crate or workspace target
4. `cargo clippy --all-targets -- -D warnings`

Native WebView2 and GPU testing remains a separate user-assisted gate because
it depends on the target Windows adapter and current GPU workload. No Docker,
release, production image, or eager Voice Worker startup is required for this
change.
