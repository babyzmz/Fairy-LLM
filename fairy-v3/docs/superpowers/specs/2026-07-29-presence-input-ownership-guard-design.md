# Presence Native Automation Input Ownership Guard

Date: 2026-07-29  
Status: Approved design  
Scope: Fairy V3 Windows native Presence regression and soak automation

## Purpose

Fairy's native Presence verification must not compete with a person who is
using the desktop. The current native regression script moves the pointer,
focuses Fairy windows, clicks, and drags. The soak path can move the pointer
once per second when `-KeepPresenceActive` is enabled, and the benchmark enables
that mode. Those actions are valid native acceptance probes only while the
automation has uncontested input ownership.

The scripts already clean up their owned processes, temporary profiles, and GPU
preference changes in `finally` blocks. This design adds a shared, fail-closed
input ownership guard without weakening those cleanup paths or replacing real
native hit-testing with synthetic DOM assertions.

## Locked behavior

- Native input automation requires at least 5,000 milliseconds of continuous
  user-input idle time before it may launch or inject input.
- Five seconds is the minimum, not merely the default. Callers may request a
  longer idle interval but cannot lower it below five seconds.
- After ownership begins, every foreground change, cursor movement, keyboard
  input, or pointer input not accounted for by Fairy's own automation ends the
  native probe immediately.
- Detection is fail-closed. The scripts never wait for the user to become idle,
  reclaim the pointer, retry the action, or continue a partial acceptance run.
- The failure code is `PRESENCE_USER_INPUT_COMPETITION`. If Windows input state
  cannot be observed reliably, the separate failure code is
  `PRESENCE_INPUT_GUARD_UNAVAILABLE`.
- A competition failure is not a native acceptance pass. The operator may use
  read-only inspection or Playwright while the desktop is active, then rerun
  the native gate in an idle window.
- There is no release-mode bypass, environment-variable escape hatch, or
  parameter that disables the guard.

## Architecture

Add one shared PowerShell helper:

`scripts/presence-input-ownership-guard.ps1`

The helper has two layers:

1. A small Windows observation adapter uses `GetLastInputInfo`, `GetCursorPos`,
   and `GetForegroundWindow`. It returns an in-memory observation containing
   the unsigned last-input tick, calculated idle milliseconds, cursor
   coordinates, and foreground window handle.
2. Pure PowerShell state transitions compare observations and maintain the
   automation-owned baseline. Tests can pass synthetic observations without
   moving the real pointer or opening a window.

The guard state contains:

- the minimum idle interval;
- the last accepted Windows input tick;
- the last automation-owned cursor position;
- the last automation-owned foreground handle;
- whether input ownership has started.

It contains no key values, text, window titles, process arguments, captions, or
screen content.

## Ownership flow

### Acquisition

Both native scripts dot-source the shared helper before creating scratch
profiles or launching Fairy.

The script reads one Windows observation and acquires ownership only when:

- the observer succeeded;
- idle time is at least 5,000 milliseconds;
- the last-input tick and cursor position are internally valid.

The current cursor position and foreground handle become the initial baseline.
Fairy startup may legitimately create or foreground an owned window. Before
adopting that transition, the script requires the input tick and cursor to
remain unchanged and verifies that the new foreground handle belongs to the
exact Fairy process started by the probe. A foreground transition to any other
process is competition. The script performs another full competition check
before its first focus or pointer action.

### Before an owned action

Immediately before `SetCursorPos`, `SetForegroundWindow`, mouse down, mouse up,
click, drag step, or the soak's `MoveToRenderWindow`, the script reads a fresh
observation.

The guard rejects the action when:

- the last-input tick changed since the last accepted baseline;
- the cursor moved away from the last automation-owned position;
- the foreground window changed away from the last automation-owned handle;
- the observation adapter failed.

Foreground comparison can be explicitly re-adopted only when the new handle is
verified against the exact Fairy process owned by the probe, or immediately
after the probe itself successfully foregrounds an already verified Fairy
window. Arbitrary foreground windows are never adopted merely to keep a test
running.

### After an owned action

After a successful owned action, the script immediately reads a new
observation and marks that exact last-input tick, cursor position, and
foreground handle as automation-owned. This prevents `SetCursorPos` or mouse
injection from being mistaken for later user activity while preserving
detection of input that occurs between actions.

Cursor-hold and drag loops apply this sequence to every movement step. A person
moving the pointer or pressing a key during a loop therefore stops the loop
instead of being overwritten until the loop finishes.

### Cleanup

Safety cleanup is unconditional:

- an outstanding automation mouse-down is released in `finally`;
- Fairy, WebView2, Vite, and probe processes owned by the script are stopped;
- temporary profiles are removed through the existing contained-path checks;
- temporary GPU preference changes are restored.

Cleanup does not attempt to reacquire input ownership and does not move or focus
the pointer. Owned-window `WM_CLOSE` and process termination are allowed only as
cleanup actions.

## Script integration

### `test-presence-native.ps1`

- Add `MinimumUserIdleSeconds` with a validation range of 5 through 60 and a
  default of 5.
- Acquire ownership before starting Vite or Fairy.
- Route every focus, cursor, click, hold, and drag operation through guarded
  wrappers.
- Check competition during window-startup waits and before non-cleanup native
  state transitions.
- Preserve `-KeepRunning` only for successful runs. A competition or guard
  failure always cleans up; it cannot leave the test application running.

### `test-presence-soak.ps1`

- Add the same bounded idle parameter.
- Acquire ownership only when `-KeepPresenceActive` requests pointer movement.
  A read-only soak without that switch does not claim or monitor keyboard and
  pointer ownership.
- Guard every initial, warm-up, and steady-state pointer placement.
- A competition failure produces no passing soak result and propagates a
  nonzero exit after cleanup.

### `test-presence-benchmark.ps1`

- Forward the idle threshold to each soak invocation.
- Stop the benchmark at the first competition failure. Do not average partial
  samples into a passing benchmark.

## Diagnostics and privacy

Diagnostics are content-free and bounded. They may include only:

- the stable failure code;
- one enumerated reason such as `startup_idle_below_threshold`,
  `last_input_changed`, `cursor_changed`, `foreground_changed`, or
  `observation_failed`;
- the configured idle threshold;
- the phase name, such as `startup`, `hover`, `drag`, `warmup`, or `soak`.

They must not include coordinates, window handles, window titles, key codes,
typed text, screenshots, process command lines, or input payloads. The helper
does not write a persistent input history.

## Testing

Add a deterministic PowerShell test for the shared helper. It uses synthetic
observations and never calls an input API. Required cases:

- 4,999 milliseconds idle is rejected and 5,000 is accepted;
- a caller cannot configure a threshold below five seconds;
- unchanged observations preserve ownership;
- a changed last-input tick is rejected;
- cursor drift is rejected;
- unexpected foreground drift is rejected;
- a changed foreground can be adopted only when the injected ownership checker
  identifies it as a window of the exact owned Fairy process;
- an explicitly owned post-action observation advances the baseline;
- a failed observer returns `PRESENCE_INPUT_GUARD_UNAVAILABLE`;
- unsigned tick arithmetic remains correct across the 32-bit tick wrap;
- diagnostics contain no coordinates, handles, titles, or input content.

Add structural integration assertions proving that native and active-soak input
paths use the shared guard and that the benchmark forwards the threshold.
Include the deterministic test in `scripts/test-all.ps1`.

Focused verification order:

1. deterministic guard tests;
2. PowerShell parser validation for all changed scripts;
3. release/script boundary checks;
4. `git diff --check`.

A real native run is performed only when the desktop has already met the
five-second idle requirement. Detection of user input is an expected blocked
result, not a reason to weaken or retry the guard.

## Out of scope

- Changing Fairy product input ownership or desktop-pet gestures;
- replacing native hit-testing with Playwright;
- collecting global input content;
- installing hooks, drivers, or background services;
- automatically locking the desktop or suppressing user input;
- weakening the Phase 8 native, privacy, performance, or four-hour gates.
