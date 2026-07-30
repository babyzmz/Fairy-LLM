# Realtime Verification Progress UX Design

**Date:** 2026-07-30
**Status:** Approved
**Scope:** Fairy V3 Desktop Local MiniCPM-o readiness card

## Problem

After the 6.8 GB model download reaches 100%, Fairy hashes every managed model
artifact, validates the promoted layout, and runs the local runtime self-test.
The readiness card currently keeps rendering `received_bytes / total_bytes`
during all active phases. The initial verification state inherits the completed
download byte count, so it displays `Verifying 100%` while the largest file is
still being hashed. That truthful download value is misleading as verification
progress and makes a healthy multi-minute integrity check look frozen.

## Decision

- Keep a determinate percentage only for `downloading`.
- Render `checking_space`, `cancelling`, `verifying`, `layout_check`, and
  `runtime_self_test` as indeterminate operations with no `aria-valuenow`.
- Give every non-download phase a specific explanation:
  - checking space: checking disk capacity before download;
  - cancelling: stopping safely while preserving resumable files;
  - verifying: checking model integrity and warning that large files can take
    several minutes;
  - layout check: finalizing the verified model layout;
  - runtime self-test: testing CUDA/model startup without starting a Realtime
    Session.
- Use a restrained moving segment inside the existing three-pixel track.
  Reduced Motion uses a static segment and no animation.
- Preserve cancellation, 750 ms authoritative readiness polling, final event
  refetch, model state, Core/Tauri contracts, and installer behavior.

## Accessibility

The downloading progressbar keeps `aria-valuemin`, `aria-valuemax`, and
`aria-valuenow`. Indeterminate phases keep the progressbar role and accessible
label but omit all numeric ARIA values. Visible text replaces the inaccurate
percentage with a short phase status such as `Integrity check`.

## Testing

Component tests must prove:

- a 50% download remains determinate and cancellable;
- verification renders no `aria-valuenow`, no `100%`, and explains that the
  integrity check can take several minutes;
- runtime self-test is indeterminate and explicitly says it does not start a
  Realtime Session.

Validation is limited to the affected Vitest suite, TypeScript, CSS/diff review,
and live Vite hot reload. No model re-download, native runtime restart, Docker,
release build, Voice Worker, or Realtime Worker is required.
