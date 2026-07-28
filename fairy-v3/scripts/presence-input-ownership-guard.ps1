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

    return [PSCustomObject]@{
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

function Sync-PresenceInputOwnership(
    [Parameter(Mandatory)]$State,
    [Parameter(Mandatory)]$Observation,
    [Parameter(Mandatory)][int]$OwnedProcessId,
    [Parameter(Mandatory)][string]$Phase
) {
    if (-not [bool]$State.acquired) {
        throw (
            "PRESENCE_USER_INPUT_COMPETITION: " +
            "not_acquired phase=$Phase"
        )
    }

    if ([uint32]$Observation.input_tick -ne [uint32]$State.input_tick) {
        throw (
            "PRESENCE_USER_INPUT_COMPETITION: " +
            "last_input_changed phase=$Phase"
        )
    }

    if (
        [int]$Observation.cursor_x -ne [int]$State.cursor_x -or
        [int]$Observation.cursor_y -ne [int]$State.cursor_y
    ) {
        throw (
            "PRESENCE_USER_INPUT_COMPETITION: " +
            "cursor_changed phase=$Phase"
        )
    }

    if ([long]$Observation.foreground_handle -ne
        [long]$State.foreground_handle) {
        if (
            $OwnedProcessId -le 0 -or
            [int]$Observation.foreground_process_id -ne $OwnedProcessId
        ) {
            throw (
                "PRESENCE_USER_INPUT_COMPETITION: " +
                "foreground_changed phase=$Phase"
            )
        }
        $State.foreground_handle = [long]$Observation.foreground_handle
    }
}

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
        [long]$after.foreground_handle -ne
            [long]$State.foreground_handle -and
        (
            $OwnedProcessId -le 0 -or
            [int]$after.foreground_process_id -ne $OwnedProcessId
        )
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
