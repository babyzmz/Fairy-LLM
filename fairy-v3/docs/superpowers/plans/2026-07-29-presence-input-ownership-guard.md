# Presence Native Automation Input Ownership Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. The user
> has forbidden subagents, so execution must remain in the primary thread.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Fairy native Presence regression, active soak, and benchmark
automation immediately when Windows reports competing user input.

**Architecture:** A shared PowerShell helper separates read-only Win32
observation from pure ownership state transitions. Native and soak scripts
acquire ownership after five seconds of idle time, synchronize only verified
Fairy-owned foreground transitions, and wrap every focus or pointer action in
pre-action and post-action observations.

**Tech Stack:** Windows PowerShell 5.1, C# P/Invoke through `Add-Type`, Win32
`GetLastInputInfo`/`GetCursorPos`/`GetForegroundWindow`, deterministic
PowerShell tests, existing Fairy native Presence probes.

## Global Constraints

- The minimum continuous idle interval is 5,000 milliseconds.
- Callers may configure 5 through 60 seconds and cannot disable the guard.
- Competition fails with `PRESENCE_USER_INPUT_COMPETITION`.
- Observer failure fails with `PRESENCE_INPUT_GUARD_UNAVAILABLE`.
- No diagnostic may contain coordinates, window handles, titles, key values,
  typed text, screenshots, captions, or command lines.
- Only a foreground handle owned by the exact Fairy process launched by the
  probe may be adopted without an automation action.
- Mouse-up, process termination, scratch cleanup, and GPU preference
  restoration always run after a failure.
- `-KeepRunning` preserves processes only after a completely successful native
  probe.
- A read-only soak without `-KeepPresenceActive` does not claim keyboard or
  pointer ownership.
- Do not weaken native, privacy, performance, installer, signing, or four-hour
  Phase 8 gates.
- Do not mix the native PowerShell smoke with Computer Use or any second UI
  automation owner in the same run.
- Do not modify or stage the user's untracked `CLAUDE.md`.

## File structure

- Create `scripts/presence-input-ownership-guard.ps1`: live Windows observation
  adapter and pure guard state transitions.
- Create `scripts/test-presence-input-ownership-guard.ps1`: deterministic
  synthetic-observation tests and structural integration assertions.
- Modify `scripts/test-all.ps1`: execute the deterministic guard test in the
  normal release-script gate.
- Modify `scripts/test-presence-native.ps1`: guard native startup, focus,
  cursor hold, click, drag, and success-only `KeepRunning`.
- Modify `scripts/test-presence-soak.ps1`: guard cursor retention only when
  `KeepPresenceActive` is enabled.
- Modify `scripts/test-presence-benchmark.ps1`: forward the threshold, reject
  competition immediately, and prevent stale partial samples.
- Modify `docs/acceptance/realtime-companion-beta-phase-8.md`: record the new
  automation safety boundary without claiming a native acceptance pass.

---

### Task 1: Shared observation and ownership state machine

**Files:**

- Create: `scripts/presence-input-ownership-guard.ps1`
- Create: `scripts/test-presence-input-ownership-guard.ps1`
- Modify: `scripts/test-all.ps1:107-145`

**Interfaces:**

- Produces:
  `New-PresenceInputGuardState -MinimumIdleSeconds <int>`.
- Produces:
  `Get-PresenceInputObservation -Phase <string> [-Observer <scriptblock>]`.
- Produces:
  `Get-PresenceInputIdleMilliseconds -CurrentTick <uint32>
  -LastInputTick <uint32>`.
- Produces:
  `Acquire-PresenceInputOwnership -State <object> -Observation <object>
  -Phase <string>`.
- Produces:
  `Sync-PresenceInputOwnership -State <object> -Observation <object>
  -OwnedProcessId <int> -Phase <string>`.
- Produces:
  `Invoke-PresenceOwnedInputAction -State <object> -OwnedProcessId <int>
  -Phase <string> -Action <scriptblock> [-Observer <scriptblock>]`.
- The observation shape is exactly
  `input_tick`, `idle_milliseconds`, `cursor_x`, `cursor_y`,
  `foreground_handle`, and `foreground_process_id`.
- Later tasks consume only these functions and do not call
  `GetLastInputInfo` directly.

- [ ] **Step 1: Write the failing deterministic test**

Create `scripts/test-presence-input-ownership-guard.ps1` with strict mode,
small local assertion helpers, and synthetic observations:

```powershell
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$helper = Join-Path $PSScriptRoot "presence-input-ownership-guard.ps1"
. $helper

function New-TestObservation(
    [uint32]$InputTick,
    [long]$IdleMilliseconds,
    [int]$CursorX = 100,
    [int]$CursorY = 200,
    [long]$ForegroundHandle = 300,
    [int]$ForegroundProcessId = 400
) {
    [PSCustomObject]@{
        input_tick = $InputTick
        idle_milliseconds = $IdleMilliseconds
        cursor_x = $CursorX
        cursor_y = $CursorY
        foreground_handle = $ForegroundHandle
        foreground_process_id = $ForegroundProcessId
    }
}

function Assert-ThrowsCode(
    [scriptblock]$Action,
    [string]$Code
) {
    try {
        & $Action
    }
    catch {
        if (-not $_.Exception.Message.StartsWith("${Code}:")) {
            throw "Expected $Code, got: $($_.Exception.Message)"
        }
        return $_.Exception.Message
    }
    throw "Expected $Code, but the action succeeded"
}

$below = New-PresenceInputGuardState -MinimumIdleSeconds 5
$belowMessage = Assert-ThrowsCode {
    Acquire-PresenceInputOwnership `
        -State $below `
        -Observation (New-TestObservation 10 4999) `
        -Phase "startup"
} "PRESENCE_USER_INPUT_COMPETITION"
if ($belowMessage -notmatch "startup_idle_below_threshold") {
    throw "The startup reason is not bounded"
}

$state = New-PresenceInputGuardState -MinimumIdleSeconds 5
Acquire-PresenceInputOwnership `
    -State $state `
    -Observation (New-TestObservation 10 5000) `
    -Phase "startup"
Sync-PresenceInputOwnership `
    -State $state `
    -Observation (New-TestObservation 10 5100) `
    -OwnedProcessId 400 `
    -Phase "steady"

Assert-ThrowsCode {
    Sync-PresenceInputOwnership `
        -State $state `
        -Observation (New-TestObservation 11 0) `
        -OwnedProcessId 400 `
        -Phase "steady"
} "PRESENCE_USER_INPUT_COMPETITION" | Out-Null

Assert-ThrowsCode {
    Sync-PresenceInputOwnership `
        -State $state `
        -Observation (New-TestObservation 10 5100 101 200) `
        -OwnedProcessId 400 `
        -Phase "steady"
} "PRESENCE_USER_INPUT_COMPETITION" | Out-Null

Assert-ThrowsCode {
    Sync-PresenceInputOwnership `
        -State $state `
        -Observation (New-TestObservation 10 5100 100 200 301 401) `
        -OwnedProcessId 400 `
        -Phase "steady"
} "PRESENCE_USER_INPUT_COMPETITION" | Out-Null

$ownedForeground = New-TestObservation 10 5100 100 200 302 400
Sync-PresenceInputOwnership `
    -State $state `
    -Observation $ownedForeground `
    -OwnedProcessId 400 `
    -Phase "startup"
if ($state.foreground_handle -ne 302) {
    throw "Owned Fairy foreground transition was not adopted"
}

if ((Get-PresenceInputIdleMilliseconds 10 ([uint32]::MaxValue - 5)) -ne 16) {
    throw "Unsigned input tick wrap was calculated incorrectly"
}

Assert-ThrowsCode {
    New-PresenceInputGuardState -MinimumIdleSeconds 4
} "PRESENCE_INPUT_GUARD_UNAVAILABLE" | Out-Null

$observerMessage = Assert-ThrowsCode {
    Get-PresenceInputObservation `
        -Phase "observer_test" `
        -Observer { throw "private cursor 12 title secret" }
} "PRESENCE_INPUT_GUARD_UNAVAILABLE"
if ($observerMessage -ne (
    "PRESENCE_INPUT_GUARD_UNAVAILABLE: " +
    "observation_failed phase=observer_test"
)) {
    throw "Observer diagnostics were not bounded"
}

$actionState = New-PresenceInputGuardState -MinimumIdleSeconds 5
Acquire-PresenceInputOwnership `
    -State $actionState `
    -Observation (New-TestObservation 20 5000) `
    -Phase "action"
$observations = [System.Collections.Generic.Queue[object]]::new()
$observations.Enqueue((New-TestObservation 20 5100))
$observations.Enqueue((New-TestObservation 21 0 105 205 300 400))
$actionResult = Invoke-PresenceOwnedInputAction `
    -State $actionState `
    -OwnedProcessId 400 `
    -Phase "action" `
    -Observer { $observations.Dequeue() } `
    -Action { "owned-result" }
if (
    $actionResult -ne "owned-result" -or
    $actionState.input_tick -ne 21 -or
    $actionState.cursor_x -ne 105 -or
    $actionState.cursor_y -ne 205
) {
    throw "Owned post-action observation did not advance the baseline"
}

foreach ($message in @(
    $belowMessage,
    $observerMessage
)) {
    foreach ($forbidden in @(
        "cursor_x",
        "cursor_y",
        "foreground_handle",
        "title",
        "key",
        "text",
        "command_line"
    )) {
        if ($message -match [regex]::Escape($forbidden)) {
            throw "Guard diagnostic exposed forbidden field: $forbidden"
        }
    }
}

Write-Output "Presence input ownership guard tests passed."
```

- [ ] **Step 2: Run the test and verify the helper is missing**

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/test-presence-input-ownership-guard.ps1
```

Expected: FAIL while dot-sourcing the missing
`scripts/presence-input-ownership-guard.ps1`.

- [ ] **Step 3: Implement the read-only Windows adapter**

Create `scripts/presence-input-ownership-guard.ps1` with strict mode and one
guarded `Add-Type` block:

```powershell
Set-StrictMode -Version Latest

if (-not ("FairyPresenceInputObserver" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public sealed class FairyPresenceInputObservation {
    public uint InputTick { get; set; }
    public long IdleMilliseconds { get; set; }
    public int CursorX { get; set; }
    public int CursorY { get; set; }
    public long ForegroundHandle { get; set; }
    public int ForegroundProcessId { get; set; }
}

public static class FairyPresenceInputObserver {
    [StructLayout(LayoutKind.Sequential)]
    private struct LASTINPUTINFO {
        public uint cbSize;
        public uint dwTime;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT {
        public int X;
        public int Y;
    }

    [DllImport("user32.dll")]
    private static extern bool GetLastInputInfo(ref LASTINPUTINFO value);
    [DllImport("user32.dll")]
    private static extern bool GetCursorPos(out POINT point);
    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(
        IntPtr window,
        out uint processId
    );

    public static FairyPresenceInputObservation Observe() {
        var input = new LASTINPUTINFO {
            cbSize = (uint)Marshal.SizeOf<LASTINPUTINFO>()
        };
        if (!GetLastInputInfo(ref input)) {
            throw new InvalidOperationException("last_input_unavailable");
        }
        POINT cursor;
        if (!GetCursorPos(out cursor)) {
            throw new InvalidOperationException("cursor_unavailable");
        }
        var foreground = GetForegroundWindow();
        uint processId = 0;
        if (foreground != IntPtr.Zero) {
            GetWindowThreadProcessId(foreground, out processId);
        }
        var current = unchecked((uint)Environment.TickCount);
        var idle = current >= input.dwTime
            ? (ulong)(current - input.dwTime)
            : (ulong)uint.MaxValue - input.dwTime + 1UL + current;
        return new FairyPresenceInputObservation {
            InputTick = input.dwTime,
            IdleMilliseconds = (long)idle,
            CursorX = cursor.X,
            CursorY = cursor.Y,
            ForegroundHandle = foreground.ToInt64(),
            ForegroundProcessId = unchecked((int)processId)
        };
    }
}
'@
}
```

`Get-PresenceInputObservation` accepts an optional observer for deterministic
tests and otherwise invokes the live adapter:

```powershell
function Get-PresenceInputObservation(
    [Parameter(Mandatory)][string]$Phase,
    [scriptblock]$Observer = $null
) {
    try {
        if ($null -eq $Observer) {
            $native = [FairyPresenceInputObserver]::Observe()
            $observed = [PSCustomObject]@{
                input_tick = $native.InputTick
                idle_milliseconds = $native.IdleMilliseconds
                cursor_x = $native.CursorX
                cursor_y = $native.CursorY
                foreground_handle = $native.ForegroundHandle
                foreground_process_id = $native.ForegroundProcessId
            }
        }
        else {
            $observed = & $Observer
        }
        return [PSCustomObject]@{
            input_tick = [uint32]$observed.input_tick
            idle_milliseconds = [long]$observed.idle_milliseconds
            cursor_x = [int]$observed.cursor_x
            cursor_y = [int]$observed.cursor_y
            foreground_handle = [long]$observed.foreground_handle
            foreground_process_id = [int]$observed.foreground_process_id
        }
    }
    catch {
        throw (
            "PRESENCE_INPUT_GUARD_UNAVAILABLE: " +
            "observation_failed phase=$Phase"
        )
    }
}
```

It maps every adapter exception to:

```text
PRESENCE_INPUT_GUARD_UNAVAILABLE: observation_failed phase=<phase>
```

The message must not append the original Win32 exception.

- [ ] **Step 4: Implement pure ownership transitions**

Implement the following exact functions:

```powershell
function Get-PresenceInputIdleMilliseconds(
    [uint32]$CurrentTick,
    [uint32]$LastInputTick
) {
    if ($CurrentTick -ge $LastInputTick) {
        return [long]($CurrentTick - $LastInputTick)
    }
    return [long](
        ([uint64][uint32]::MaxValue - $LastInputTick) +
        1 +
        $CurrentTick
    )
}

function New-PresenceInputGuardState(
    [int]$MinimumIdleSeconds = 5
) {
    if ($MinimumIdleSeconds -lt 5 -or $MinimumIdleSeconds -gt 60) {
        throw (
            "PRESENCE_INPUT_GUARD_UNAVAILABLE: " +
            "idle_threshold_out_of_range phase=configuration"
        )
    }
    [PSCustomObject]@{
        minimum_idle_milliseconds = [long]$MinimumIdleSeconds * 1000
        acquired = $false
        input_tick = [uint32]0
        cursor_x = 0
        cursor_y = 0
        foreground_handle = [long]0
    }
}

function Acquire-PresenceInputOwnership(
    [Parameter(Mandatory)]$State,
    [Parameter(Mandatory)]$Observation,
    [Parameter(Mandatory)][string]$Phase
) {
    if ([long]$Observation.idle_milliseconds -lt
        [long]$State.minimum_idle_milliseconds) {
        throw (
            "PRESENCE_USER_INPUT_COMPETITION: " +
            "startup_idle_below_threshold phase=$Phase"
        )
    }
    $State.acquired = $true
    $State.input_tick = [uint32]$Observation.input_tick
    $State.cursor_x = [int]$Observation.cursor_x
    $State.cursor_y = [int]$Observation.cursor_y
    $State.foreground_handle = [long]$Observation.foreground_handle
}
```

`Sync-PresenceInputOwnership` must check in this order:

1. state was acquired;
2. input tick is unchanged;
3. cursor coordinates are unchanged;
4. foreground is unchanged, or its process id equals the nonzero
   `OwnedProcessId`.

It adopts only the fourth case. Each failure emits the corresponding bounded
reason: `not_acquired`, `last_input_changed`, `cursor_changed`, or
`foreground_changed`.

`Invoke-PresenceOwnedInputAction` accepts the same optional observer and must:

```powershell
function Invoke-PresenceOwnedInputAction(
    [Parameter(Mandatory)]$State,
    [Parameter(Mandatory)][int]$OwnedProcessId,
    [Parameter(Mandatory)][string]$Phase,
    [Parameter(Mandatory)][scriptblock]$Action,
    [scriptblock]$Observer = $null
) {
$before = Get-PresenceInputObservation `
    -Phase $Phase `
    -Observer $Observer
Sync-PresenceInputOwnership `
    -State $State `
    -Observation $before `
    -OwnedProcessId $OwnedProcessId `
    -Phase $Phase
$result = & $Action
$after = Get-PresenceInputObservation `
    -Phase $Phase `
    -Observer $Observer
if (
    [long]$after.foreground_handle -ne [long]$State.foreground_handle -and
    [int]$after.foreground_process_id -ne $OwnedProcessId
) {
    throw (
        "PRESENCE_USER_INPUT_COMPETITION: " +
        "foreground_changed phase=$Phase"
    )
}
$State.input_tick = [uint32]$after.input_tick
$State.cursor_x = [int]$after.cursor_x
$State.cursor_y = [int]$after.cursor_y
$State.foreground_handle = [long]$after.foreground_handle
return $result
}
```

- [ ] **Step 5: Run deterministic tests**

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/test-presence-input-ownership-guard.ps1
```

Expected: PASS with exactly
`Presence input ownership guard tests passed.`

- [ ] **Step 6: Add the focused gate to `test-all.ps1`**

Insert after the Voice release-license policy:

```powershell
Invoke-Step "Presence: input ownership guard" $Root "powershell" @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts/test-presence-input-ownership-guard.ps1"
)
```

- [ ] **Step 7: Validate parsers and commit**

Run:

```powershell
$paths = @(
  "scripts/presence-input-ownership-guard.ps1",
  "scripts/test-presence-input-ownership-guard.ps1",
  "scripts/test-all.ps1"
)
foreach ($path in $paths) {
  [void][scriptblock]::Create((Get-Content -Raw -LiteralPath $path))
}
git diff --check
```

Expected: no parser exception and exit 0.

Commit:

```powershell
git add -- `
  scripts/presence-input-ownership-guard.ps1 `
  scripts/test-presence-input-ownership-guard.ps1 `
  scripts/test-all.ps1
git commit -m "feat(release): add presence input ownership guard"
```

---

### Task 2: Guard the native Presence regression lifecycle

**Files:**

- Modify: `scripts/test-presence-native.ps1:1-11`
- Modify: `scripts/test-presence-native.ps1:177-314`
- Modify: `scripts/test-presence-native.ps1:425-754`
- Modify: `scripts/test-presence-input-ownership-guard.ps1`

**Interfaces:**

- Consumes all guard functions from Task 1.
- Produces guarded local wrappers:
  `Set-GuardedNativeCursor`, `Focus-GuardedNativeWindow`,
  `Invoke-GuardedNativeClick`, `Invoke-GuardedNativeDrag`, and
  `Hold-GuardedNativeCursor`.
- Produces `$nativeProbeSucceeded`, which is the only state that permits
  `-KeepRunning`.

- [ ] **Step 1: Add failing native structural tests**

Extend `scripts/test-presence-input-ownership-guard.ps1`:

```powershell
$nativePath = Join-Path $PSScriptRoot "test-presence-native.ps1"
$native = Get-Content -Raw -LiteralPath $nativePath
foreach ($required in @(
    "MinimumUserIdleSeconds",
    "New-PresenceInputGuardState",
    "Acquire-PresenceInputOwnership",
    "Invoke-PresenceOwnedInputAction",
    "Sync-PresenceInputOwnership",
    "nativeProbeSucceeded",
    "Hold-GuardedNativeCursor",
    "Invoke-GuardedNativeClick",
    "Invoke-GuardedNativeDrag"
)) {
    if ($native -notmatch [regex]::Escape($required)) {
        throw "Native Presence script is missing guarded contract: $required"
    }
}
if ($native -notmatch
    '\$preserveRunning\s*=\s*\$KeepRunning\s*-and\s*\$nativeProbeSucceeded') {
    throw "KeepRunning is not restricted to a successful guarded probe"
}
```

Add this AST assertion. C# method declarations inside the here-string are not
PowerShell invocation nodes, so the check covers only executable PowerShell:

```powershell
function Get-EnclosingFunctionName($Node) {
    $current = $Node
    while ($null -ne $current) {
        if ($current -is
            [System.Management.Automation.Language.FunctionDefinitionAst]) {
            return $current.Name
        }
        $current = $current.Parent
    }
    return ""
}

$tokens = $null
$parseErrors = $null
$nativeAst = [System.Management.Automation.Language.Parser]::ParseFile(
    $nativePath,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
    throw "Native Presence script does not parse"
}
$rawMethods = @(
    "SetCursorPos",
    "Focus",
    "LeftButtonDown",
    "LeftButtonUp"
)
$guardedFunctions = @(
    "Set-GuardedNativeCursor",
    "Focus-GuardedNativeWindow",
    "Invoke-GuardedNativeClick",
    "Invoke-GuardedNativeDrag"
)
$unguarded = @($nativeAst.FindAll({
    param($node)
    $node -is
        [System.Management.Automation.Language.InvokeMemberExpressionAst] -and
    $node.Static -and
    $node.Expression.Extent.Text -eq "[FairyNativeProbe]" -and
    $node.Member.Extent.Text -in $rawMethods
}, $true) | Where-Object {
    (Get-EnclosingFunctionName $_) -notin $guardedFunctions
})
if ($unguarded.Count -ne 0) {
    throw (
        "Native Presence script contains unguarded input calls: " +
        (($unguarded.Extent.Text) -join ", ")
    )
}
```

- [ ] **Step 2: Run the test and verify native integration is absent**

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/test-presence-input-ownership-guard.ps1
```

Expected: FAIL with
`Native Presence script is missing guarded contract`.

- [ ] **Step 3: Acquire ownership before launching native processes**

Add the parameter:

```powershell
[ValidateRange(5, 60)]
[int]$MinimumUserIdleSeconds = 5
```

Dot-source the helper and acquire before creating scratch directories:

```powershell
. (Join-Path $PSScriptRoot "presence-input-ownership-guard.ps1")
$inputGuard = New-PresenceInputGuardState `
    -MinimumIdleSeconds $MinimumUserIdleSeconds
Acquire-PresenceInputOwnership `
    -State $inputGuard `
    -Observation (Get-PresenceInputObservation -Phase "startup") `
    -Phase "startup"
$nativeProbeSucceeded = $false
$mouseButtonDown = $false
```

- [ ] **Step 4: Add guarded native wrappers**

Add wrappers that call `Invoke-PresenceOwnedInputAction`:

```powershell
function Set-GuardedNativeCursor(
    $GuardState,
    [int]$OwnedProcessId,
    [int]$X,
    [int]$Y,
    [string]$Phase
) {
    $moved = Invoke-PresenceOwnedInputAction `
        -State $GuardState `
        -OwnedProcessId $OwnedProcessId `
        -Phase $Phase `
        -Action { [FairyNativeProbe]::SetCursorPos($X, $Y) }
    return [bool]$moved
}

function Focus-GuardedNativeWindow(
    $GuardState,
    [int]$OwnedProcessId,
    [IntPtr]$Handle,
    [string]$Phase
) {
    $focused = Invoke-PresenceOwnedInputAction `
        -State $GuardState `
        -OwnedProcessId $OwnedProcessId `
        -Phase $Phase `
        -Action { [FairyNativeProbe]::Focus($Handle) }
    if (-not $focused) {
        throw "Windows rejected the native foreground transition"
    }
}
```

`Hold-GuardedNativeCursor` calls `Set-GuardedNativeCursor` every 25
milliseconds and throws `Windows rejected the native cursor placement` if a
move returns false. `Invoke-GuardedNativeClick` guards button down, checks
ownership after the 35-millisecond dwell, and guards button up.
`Invoke-GuardedNativeDrag` does the same for button down, the initial
340-millisecond dwell, all six cursor steps, and button up. Each gesture wrapper
has a `finally` that performs one unguarded `LeftButtonUp` only when
`$mouseButtonDown` remains true; that unconditional release is safety cleanup
and occurs inside the guarded wrapper.

- [ ] **Step 5: Guard startup waits and all input sites**

Change `Wait-Window` to accept `GuardState` and call:

```powershell
Sync-PresenceInputOwnership `
    -State $GuardState `
    -Observation (Get-PresenceInputObservation -Phase "startup_wait") `
    -OwnedProcessId $ProcessId `
    -Phase "startup_wait"
```

Replace every direct input site:

- initial cursor availability (store the wrapper's Boolean result without
  throwing) and, when available, the two-second hold;
- main-window focus;
- hover anchor holds;
- both click-toggle sequences;
- regression drag start, button down, each of six cursor steps, and button up;
- final return-to-idle hold.

Call `Sync-PresenceInputOwnership` before non-cleanup `WM_CLOSE` transitions.
Do not guard the final cleanup mouse-up or process termination.

- [ ] **Step 6: Make `KeepRunning` success-only**

Change the existing final result expression from a pipeline into a variable:

```powershell
$result = [PSCustomObject]@{
```

Keep every existing property from `process_id` through `webview2_port`
unchanged, replace the existing closing
`} | ConvertTo-Json -Depth 4` with `}`, and then append:

```powershell
$resultJson = $result | ConvertTo-Json -Depth 4
$nativeProbeSucceeded = $true
$resultJson
```

At the beginning of `finally`:

```powershell
$preserveRunning = $KeepRunning -and $nativeProbeSucceeded
```

Use `$preserveRunning` for process, Vite, and scratch preservation. A
competition failure always terminates owned processes and removes scratch
state.

- [ ] **Step 7: Run focused tests and parser validation**

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/test-presence-input-ownership-guard.ps1

[void][scriptblock]::Create(
  (Get-Content -Raw -LiteralPath scripts/test-presence-native.ps1)
)
git diff --check
```

Expected: deterministic and structural tests pass; parser and diff checks exit
0. Do not launch Fairy in this task.

- [ ] **Step 8: Commit**

```powershell
git add -- `
  scripts/test-presence-native.ps1 `
  scripts/test-presence-input-ownership-guard.ps1
git commit -m "fix(release): stop native probes on user input"
```

---

### Task 3: Guard active soak and benchmark sampling

**Files:**

- Modify: `scripts/test-presence-soak.ps1:1-82`
- Modify: `scripts/test-presence-soak.ps1:249-471`
- Modify: `scripts/test-presence-benchmark.ps1:1-103`
- Modify: `scripts/test-presence-input-ownership-guard.ps1`

**Interfaces:**

- Consumes Task 1 guard functions.
- `test-presence-soak.ps1` accepts
  `-MinimumUserIdleSeconds <5..60>`.
- `test-presence-benchmark.ps1` accepts the same parameter and forwards it to
  every active soak.
- A benchmark competition error is rethrown unchanged and no partial result is
  averaged.

- [ ] **Step 1: Add failing soak and benchmark structural tests**

Extend the deterministic test:

```powershell
$soak = Get-Content -Raw -LiteralPath (
    Join-Path $PSScriptRoot "test-presence-soak.ps1"
)
$benchmark = Get-Content -Raw -LiteralPath (
    Join-Path $PSScriptRoot "test-presence-benchmark.ps1"
)
foreach ($required in @(
    "MinimumUserIdleSeconds",
    "Acquire-PresenceInputOwnership",
    "Invoke-PresenceOwnedInputAction"
)) {
    if ($soak -notmatch [regex]::Escape($required)) {
        throw "Presence soak is missing guarded contract: $required"
    }
}
if ($benchmark -notmatch
    '-MinimumUserIdleSeconds\s+\$MinimumUserIdleSeconds') {
    throw "Presence benchmark does not forward the idle threshold"
}
if ($benchmark -notmatch
    'PRESENCE_USER_INPUT_COMPETITION') {
    throw "Presence benchmark does not stop on input competition"
}
```

Add this AST assertion that every executable `MoveToRenderWindow` call is
nested in the action scriptblock passed to
`Invoke-PresenceOwnedInputAction`:

```powershell
$tokens = $null
$parseErrors = $null
$soakPath = Join-Path $PSScriptRoot "test-presence-soak.ps1"
$soakAst = [System.Management.Automation.Language.Parser]::ParseFile(
    $soakPath,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
    throw "Presence soak script does not parse"
}

function Test-IsGuardedActionScriptBlock($Node) {
    $current = $Node.Parent
    while ($null -ne $current) {
        if ($current -is
            [System.Management.Automation.Language.ScriptBlockExpressionAst]) {
            $command = $current.Parent
            if (
                $command -is
                    [System.Management.Automation.Language.CommandAst] -and
                $command.GetCommandName() -eq
                    "Invoke-PresenceOwnedInputAction"
            ) {
                return $true
            }
        }
        $current = $current.Parent
    }
    return $false
}

$unguardedSoakMoves = @($soakAst.FindAll({
    param($node)
    $node -is
        [System.Management.Automation.Language.InvokeMemberExpressionAst] -and
    $node.Static -and
    $node.Expression.Extent.Text -eq "[FairyPresenceSoakCursor]" -and
    $node.Member.Extent.Text -eq "MoveToRenderWindow"
}, $true) | Where-Object {
    -not (Test-IsGuardedActionScriptBlock $_)
})
if ($unguardedSoakMoves.Count -ne 0) {
    throw "Presence soak contains unguarded cursor movement"
}
```

- [ ] **Step 2: Run the test and verify soak integration is absent**

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/test-presence-input-ownership-guard.ps1
```

Expected: FAIL with
`Presence soak is missing guarded contract`.

- [ ] **Step 3: Guard `KeepPresenceActive` in the soak**

Add the bounded parameter and dot-source the helper. Initialize no guard for a
read-only soak:

```powershell
$inputGuard = $null
if ($KeepPresenceActive) {
    $inputGuard = New-PresenceInputGuardState `
        -MinimumIdleSeconds $MinimumUserIdleSeconds
    Acquire-PresenceInputOwnership `
        -State $inputGuard `
        -Observation (Get-PresenceInputObservation -Phase "startup") `
        -Phase "startup"
}
```

Replace each initial, warm-up, and steady-state call with:

```powershell
$moved = Invoke-PresenceOwnedInputAction `
    -State $inputGuard `
    -OwnedProcessId $process.Id `
    -Phase $phase `
    -Action {
        [FairyPresenceSoakCursor]::MoveToRenderWindow($process.Id)
    }
```

The initial wait retries only when `$moved` is false because the Fairy hit
proxy is not present. It never retries a guard exception. The warm-up uses
phase `warmup`; the measured loop uses phase `soak`.

- [ ] **Step 4: Forward the threshold and reject partial benchmarks**

Add the benchmark parameter:

```powershell
[ValidateRange(5, 60)]
[int]$MinimumUserIdleSeconds = 5
```

Pass it to every soak. Use a per-sample nonce:

```powershell
$samplePath = Join-Path $outputDirectory (
    "presence-$mode-$fps-$([guid]::NewGuid().ToString('N')).json"
)
```

In the catch block:

```powershell
if (
    $_.Exception.Message.StartsWith(
        "PRESENCE_USER_INPUT_COMPETITION:"
    ) -or
    $_.Exception.Message.StartsWith(
        "PRESENCE_INPUT_GUARD_UNAVAILABLE:"
    )
) {
    throw
}
$errorMessage = $_.Exception.Message
```

Track every nonce sample path and remove only those exact generated files in a
`finally` block after the aggregate result is written or the benchmark aborts.
Validate each path remains inside `$outputDirectory` before removal.

- [ ] **Step 5: Run focused tests and parser validation**

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/test-presence-input-ownership-guard.ps1

$paths = @(
  "scripts/test-presence-soak.ps1",
  "scripts/test-presence-benchmark.ps1"
)
foreach ($path in $paths) {
  [void][scriptblock]::Create((Get-Content -Raw -LiteralPath $path))
}
git diff --check
```

Expected: tests and parser checks pass with no Fairy process started.

- [ ] **Step 6: Commit**

```powershell
git add -- `
  scripts/test-presence-soak.ps1 `
  scripts/test-presence-benchmark.ps1 `
  scripts/test-presence-input-ownership-guard.ps1
git commit -m "fix(release): stop presence soaks on user input"
```

---

### Task 4: Repository certification and acceptance record

**Files:**

- Modify: `docs/acceptance/realtime-companion-beta-phase-8.md`
- No other source file changes unless a focused gate exposes an independent
  Critical or Important defect.

**Interfaces:**

- Consumes all committed guard behavior from Tasks 1 through 3.
- Produces a truthful acceptance record that distinguishes deterministic
  guard certification from a native WebView2 acceptance pass.

- [ ] **Step 1: Run the focused guard and script gates**

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/test-presence-input-ownership-guard.ps1

$paths = @(
  "scripts/presence-input-ownership-guard.ps1",
  "scripts/test-presence-input-ownership-guard.ps1",
  "scripts/test-presence-native.ps1",
  "scripts/test-presence-soak.ps1",
  "scripts/test-presence-benchmark.ps1",
  "scripts/test-all.ps1"
)
foreach ($path in $paths) {
  [void][scriptblock]::Create((Get-Content -Raw -LiteralPath $path))
}
```

Expected: focused tests pass and every script parses.

- [ ] **Step 2: Run repository boundary and release document checks**

Run:

```powershell
$env:UV_CACHE_DIR = Join-Path (
  [System.IO.Path]::GetTempPath()
) "fairy-v3-release\uv"
$uv = "C:\Python313\Scripts\uv.exe"
& $uv run --project core python scripts/check_boundaries.py .
& $uv run --project core python scripts/check-release-documents.py
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 3: Run the complete non-Docker repository gate**

Run with bounded Rust parallelism:

```powershell
$env:CARGO_BUILD_JOBS = "1"
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/test-all.ps1 `
  -SkipDocker
```

Expected: the new Presence guard step passes, all existing Core, Rust,
TypeScript, Vitest, Playwright, release, composition, performance, and contract
gates pass, and the script ends with
`All available Fairy V3 verification gates passed.`

Do not separately run Docker, `-RequireWsl`, a Tauri MSI/release build, or a
four-hour soak. `test-all.ps1 -SkipDocker` still runs its existing bounded
Core sidecar composition and release-policy checks. Docker and WSL real
integration evidence is already recorded separately.

- [ ] **Step 4: Attempt one guarded native smoke only through the new gate**

First confirm there is no existing Fairy, Core, Voice, Omni, Cargo, Vite, or
project Node process. Then run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/test-presence-native.ps1 `
  -FreshWebViewProfile `
  -VerifyRegressions `
  -MinimumUserIdleSeconds 5
```

Interpret results strictly:

- a complete pass is additional native regression evidence;
- `PRESENCE_USER_INPUT_COMPETITION` is an expected safe blocked result;
- any other error is a defect to diagnose;
- never retry automatically after competition.

The script itself owns process cleanup. Afterward, verify no controlled process
remains. This smoke does not satisfy the Phase 8 proposal/pressure/crash/
quarantine/reload matrix.

- [ ] **Step 5: Update the acceptance record**

Add this section, choosing exactly one native-smoke bullet based on the
observed result:

```markdown
## Native input ownership guard — 2026-07-29

- Deterministic certification: **PASS**. Native regression, active soak, and
  benchmark automation share a mandatory input guard with a five-second
  minimum idle threshold.
- Coverage: **PASS**. Synthetic and structural tests cover last-input tick,
  cursor drift, foreground ownership, observer failure, unsigned tick wrap,
  bounded diagnostics, guarded input sites, threshold forwarding, and
  success-only `KeepRunning`.
- Complete non-Docker repository gate: **PASS** via
  `scripts/test-all.ps1 -SkipDocker`.
- Guarded native smoke: **PASS**.
- Guarded native smoke: **SAFELY BLOCKED** with
  `PRESENCE_USER_INPUT_COMPETITION`; it was not retried.
- Scope: this certification does not convert any missing installer, signing,
  supported-GPU four-hour, native recovery/privacy, or reference-performance
  evidence to passed.
```

Delete the native-smoke bullet that does not match the run. If the smoke fails
with any other error, diagnose and fix it before editing the acceptance record.

Do not record cursor coordinates, handles, window titles, process command
lines, or user activity content.

- [ ] **Step 6: Final cleanup and diff audit**

Verify:

```powershell
Get-Process -ErrorAction SilentlyContinue |
  Where-Object {
    $_.ProcessName -in @(
      "cargo", "rustc", "fairy", "fairy-core",
      "fairy-voice", "fairy-omni", "python", "pythonw", "vite"
    )
  } |
  Select-Object ProcessName, Id, Path

git diff --check
git status --short
```

Expected: no controlled process, no `desktop/test-results`, no certification
temporary directory, and only the acceptance document is modified. The user's
`../CLAUDE.md` remains untracked and untouched.

- [ ] **Step 7: Commit**

```powershell
git add -- docs/acceptance/realtime-companion-beta-phase-8.md
git commit -m "test(release): certify presence input ownership"
```

## Completion criteria

- The shared helper and deterministic tests are committed.
- Native focus, cursor, click, and drag paths are guarded.
- Active soak and benchmark cursor retention are guarded.
- `KeepRunning` cannot survive a guard failure.
- The full non-Docker repository gate passes.
- Optional native smoke either passes or stops with the exact competition code.
- All controlled processes and temporary outputs are removed.
- Phase 8 remains blocked on any absent installer, signing, supported-GPU
  four-hour, native recovery/privacy, or reference-performance evidence.
