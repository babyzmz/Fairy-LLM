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

$notAcquired = New-PresenceInputGuardState -MinimumIdleSeconds 5
$notAcquiredMessage = Assert-ThrowsCode {
    Sync-PresenceInputOwnership `
        -State $notAcquired `
        -Observation (New-TestObservation 10 5000) `
        -OwnedProcessId 400 `
        -Phase "steady"
} "PRESENCE_USER_INPUT_COMPETITION"

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

$inputMessage = Assert-ThrowsCode {
    Sync-PresenceInputOwnership `
        -State $state `
        -Observation (New-TestObservation 11 0) `
        -OwnedProcessId 400 `
        -Phase "steady"
} "PRESENCE_USER_INPUT_COMPETITION"

$cursorMessage = Assert-ThrowsCode {
    Sync-PresenceInputOwnership `
        -State $state `
        -Observation (New-TestObservation 10 5100 101 200) `
        -OwnedProcessId 400 `
        -Phase "steady"
} "PRESENCE_USER_INPUT_COMPETITION"

$foregroundMessage = Assert-ThrowsCode {
    Sync-PresenceInputOwnership `
        -State $state `
        -Observation (New-TestObservation 10 5100 100 200 301 401) `
        -OwnedProcessId 400 `
        -Phase "steady"
} "PRESENCE_USER_INPUT_COMPETITION"

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
$upperBound = New-PresenceInputGuardState -MinimumIdleSeconds 60
if ($upperBound.minimum_idle_milliseconds -ne 60000) {
    throw "The maximum idle threshold was not retained"
}
Assert-ThrowsCode {
    New-PresenceInputGuardState -MinimumIdleSeconds 61
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
    $notAcquiredMessage,
    $inputMessage,
    $cursorMessage,
    $foregroundMessage,
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

$nativePath = Join-Path $PSScriptRoot "test-presence-native.ps1"
$native = Get-Content -Raw -LiteralPath $nativePath
foreach ($required in @(
    "MinimumUserIdleSeconds",
    "New-PresenceInputGuardState",
    "Acquire-PresenceInputOwnership",
    "Invoke-PresenceOwnedInputAction",
    "Sync-PresenceInputOwnership",
    "nativeProbeSucceeded",
    "Set-GuardedNativeCursor",
    "Focus-GuardedNativeWindow",
    "Hold-GuardedNativeCursor",
    "Invoke-GuardedNativeClick",
    "Invoke-GuardedNativeDrag",
    "Release-NativeMouseButtonForCleanup"
)) {
    if ($native -notmatch [regex]::Escape($required)) {
        throw "Native Presence script is missing guarded contract: $required"
    }
}
if ($native -notmatch
    '\$preserveRunning\s*=\s*\$KeepRunning\s*-and\s*\$nativeProbeSucceeded') {
    throw "KeepRunning is not restricted to a successful guarded probe"
}

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
    "Invoke-GuardedNativeDrag",
    "Release-NativeMouseButtonForCleanup"
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

Write-Output "Presence input ownership guard tests passed."
