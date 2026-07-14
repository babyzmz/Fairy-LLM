[CmdletBinding()]
param(
    [string]$Executable,
    [ValidateSet("liquid", "compatibility")]
    [string]$ExpectedMode = "liquid",
    [ValidateSet("auto", "power_saving", "high_performance")]
    [string]$GpuPreference = "auto",
    [int]$StartupTimeoutSeconds = 30,
    [switch]$KeepRunning
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Executable)) {
    $Executable = Join-Path $root "desktop\src-tauri\target\release\fairy.exe"
}

if ($env:OS -ne "Windows_NT") {
    throw "PRESENCE_PLATFORM_UNSUPPORTED: Windows is required"
}
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Fairy executable is missing: $Executable"
}

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public sealed class FairyWindowInfo {
    public IntPtr Handle { get; set; }
    public string Title { get; set; }
    public bool Visible { get; set; }
    public int X { get; set; }
    public int Y { get; set; }
    public int Width { get; set; }
    public int Height { get; set; }
    public long ExtendedStyle { get; set; }
}

public static class FairyNativeProbe {
    private delegate bool EnumWindowsCallback(IntPtr window, IntPtr parameter);

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)]
    private struct POINT { public int X, Y; }

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsCallback callback, IntPtr parameter);
    [DllImport("user32.dll")]
    private static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextW(IntPtr window, StringBuilder text, int capacity);
    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr window, out RECT rectangle);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")]
    private static extern IntPtr GetWindowLongPtr(IntPtr window, int index);
    [DllImport("user32.dll")]
    private static extern IntPtr WindowFromPoint(POINT point);
    [DllImport("user32.dll")]
    private static extern IntPtr GetAncestor(IntPtr window, uint flags);
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")]
    public static extern bool PostMessageW(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

    public static List<FairyWindowInfo> WindowsForProcess(int expectedProcessId) {
        var result = new List<FairyWindowInfo>();
        EnumWindows((window, _) => {
            uint processId;
            GetWindowThreadProcessId(window, out processId);
            if (processId != (uint)expectedProcessId) return true;
            var text = new StringBuilder(512);
            GetWindowTextW(window, text, text.Capacity);
            RECT rectangle;
            GetWindowRect(window, out rectangle);
            result.Add(new FairyWindowInfo {
                Handle = window,
                Title = text.ToString(),
                Visible = IsWindowVisible(window),
                X = rectangle.Left,
                Y = rectangle.Top,
                Width = rectangle.Right - rectangle.Left,
                Height = rectangle.Bottom - rectangle.Top,
                ExtendedStyle = GetWindowLongPtr(window, -20).ToInt64(),
            });
            return true;
        }, IntPtr.Zero);
        return result;
    }

    public static bool EnablePerMonitorDpiAwareness() {
        return SetProcessDpiAwarenessContext(new IntPtr(-4));
    }

    public static IntPtr RootWindowAt(int x, int y) {
        return GetAncestor(WindowFromPoint(new POINT { X = x, Y = y }), 2);
    }

    public static bool Focus(IntPtr window) {
        ShowWindow(window, 9);
        return SetForegroundWindow(window);
    }
}
'@

[FairyNativeProbe]::EnablePerMonitorDpiAwareness() | Out-Null

function Get-AvailablePort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port }
    finally { $listener.Stop() }
}

function Get-Window([int]$ProcessId, [string]$Title) {
    return [FairyNativeProbe]::WindowsForProcess($ProcessId) |
        Where-Object Title -eq $Title |
        Select-Object -First 1
}

function Wait-Window([int]$ProcessId, [string]$Title, [int]$TimeoutSeconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $window = Get-Window $ProcessId $Title
        if ($null -ne $window) { return $window }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    $available = [FairyNativeProbe]::WindowsForProcess($ProcessId) |
        ForEach-Object { "'$($_.Title)' visible=$($_.Visible) hwnd=$($_.Handle)" }
    throw "Timed out waiting for native window: $Title; available: $($available -join '; ')"
}

function Test-Style([long]$Style, [long]$Mask) {
    return (($Style -band $Mask) -eq $Mask)
}

function Hold-Cursor([int]$X, [int]$Y, [int]$DurationMilliseconds) {
    $deadline = [DateTime]::UtcNow.AddMilliseconds($DurationMilliseconds)
    do {
        if (-not [FairyNativeProbe]::SetCursorPos($X, $Y)) {
            throw "Windows rejected the native cursor placement"
        }
        Start-Sleep -Milliseconds 25
    } while ([DateTime]::UtcNow -lt $deadline)
}

function Stop-ProcessTree([System.Diagnostics.Process]$Target) {
    $Target.Refresh()
    if ($Target.HasExited) { return }
    & taskkill.exe /PID $Target.Id /T /F 2>$null | Out-Null
    if (-not $Target.WaitForExit(5000)) {
        $Target.Kill()
        $Target.WaitForExit()
    }
}

function Remove-VerifiedScratchDirectory([string]$Path, [string]$ExpectedPrefix) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $separator = [System.IO.Path]::DirectorySeparatorChar
    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd($separator)
    $resolved = [System.IO.Path]::GetFullPath($Path).TrimEnd($separator)
    $requiredRoot = $tempRoot + $separator
    $leaf = [System.IO.Path]::GetFileName($resolved)
    if (-not $resolved.StartsWith($requiredRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $leaf.StartsWith($ExpectedPrefix, [System.StringComparison]::Ordinal)) {
        throw "Refusing to remove an unverified Presence scratch directory: $resolved"
    }
    $item = Get-Item -Force -LiteralPath $resolved
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to remove a reparse-point Presence scratch directory: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

$scratchPrefix = "fairy-presence-native-"
$scratch = [System.IO.Path]::GetFullPath(
    (Join-Path ([System.IO.Path]::GetTempPath()) ($scratchPrefix + [guid]::NewGuid().ToString("N")))
)
$localAppData = Join-Path $scratch "LocalAppData"
$appData = Join-Path $scratch "AppData"
$fairyData = Join-Path $scratch "FairyData"
$webViewData = Join-Path $scratch "WebView2"
New-Item -ItemType Directory -Force -Path $localAppData, $appData, $fairyData, $webViewData | Out-Null
$port = Get-AvailablePort
$process = $null
$resolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
$gpuRegistryPath = "HKCU:\Software\Microsoft\DirectX\UserGpuPreferences"
$previousGpuPreference = $null
$hadGpuPreference = $false

if ($GpuPreference -ne "auto") {
    New-Item -Force -Path $gpuRegistryPath | Out-Null
    $gpuProperties = Get-ItemProperty -LiteralPath $gpuRegistryPath -ErrorAction SilentlyContinue
    if ($null -ne $gpuProperties) {
        $previousProperty = $gpuProperties.PSObject.Properties[$resolvedExecutable]
        if ($null -ne $previousProperty) {
            $hadGpuPreference = $true
            $previousGpuPreference = [string]$previousProperty.Value
        }
    }
    $gpuValue = if ($GpuPreference -eq "power_saving") { "GpuPreference=1;" } else { "GpuPreference=2;" }
    Set-ItemProperty -LiteralPath $gpuRegistryPath -Name $resolvedExecutable -Value $gpuValue
}

try {
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $resolvedExecutable
    $startInfo.WorkingDirectory = Split-Path -Parent $startInfo.FileName
    $startInfo.UseShellExecute = $false
    $startInfo.Environment["LOCALAPPDATA"] = $localAppData
    $startInfo.Environment["APPDATA"] = $appData
    $startInfo.Environment["FAIRY_DESKTOP_DATA_DIR"] = $fairyData
    $startInfo.Environment["WEBVIEW2_USER_DATA_FOLDER"] = $webViewData
    $webViewArguments = "--remote-debugging-port=$port"
    if ($GpuPreference -eq "power_saving") {
        $webViewArguments += " --force_low_power_gpu"
    } elseif ($GpuPreference -eq "high_performance") {
        $webViewArguments += " --force_high_performance_gpu"
    }
    if ($ExpectedMode -eq "compatibility") {
        $webViewArguments += " --disable-gpu --disable-software-rasterizer"
    }
    $startInfo.Environment["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = $webViewArguments
    $process = [System.Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) { throw "Fairy process did not start" }

    $main = Wait-Window $process.Id "Fairy" $StartupTimeoutSeconds
    $render = Wait-Window $process.Id "Fairy Presence Renderer" $StartupTimeoutSeconds
    $input = Wait-Window $process.Id "Fairy Presence Input" $StartupTimeoutSeconds
    Hold-Cursor ($main.X + 80) ($main.Y + 80) 2000
    $main = Get-Window $process.Id "Fairy"
    $render = Get-Window $process.Id "Fairy Presence Renderer"
    $input = Get-Window $process.Id "Fairy Presence Input"
    if (-not $main.Visible -or -not $render.Visible -or $input.Visible) {
        throw "Unexpected passive Fairy visibility: main=$($main.Visible), render=$($render.Visible), input=$($input.Visible)"
    }

    $WS_EX_TOPMOST = 0x00000008L
    $WS_EX_NOACTIVATE = 0x08000000L
    foreach ($mask in @($WS_EX_TOPMOST, $WS_EX_NOACTIVATE)) {
        if (-not (Test-Style $render.ExtendedStyle $mask)) {
            throw ("pet-render is missing extended style 0x{0:X}" -f $mask)
        }
    }
    if (-not (Test-Style $input.ExtendedStyle $WS_EX_TOPMOST)) {
        throw ("pet-input did not start passive and topmost: extended_style=0x{0:X}" -f $input.ExtendedStyle)
    }

    $probeScript = Join-Path $root "desktop\scripts\probe-presence-webview.mjs"
    $readyOutput = & node $probeScript --port $port --expected-mode $ExpectedMode --duration-seconds 0
    if ($LASTEXITCODE -ne 0) { throw "WebView2 presence readiness probe failed" }
    $readyProbe = ($readyOutput -join "`n") | ConvertFrom-Json
    $render = Get-Window $process.Id "Fairy Presence Renderer"
    $expectedRenderX = [int][Math]::Round([double]$readyProbe.final.placement.render_x)
    $expectedRenderY = [int][Math]::Round([double]$readyProbe.final.placement.render_y)
    if ([Math]::Abs($render.X - $expectedRenderX) -gt 2 -or
        [Math]::Abs($render.Y - $expectedRenderY) -gt 2) {
        throw "Native render placement disagrees with coordinator: hwnd=[$($render.X),$($render.Y)], expected=[$expectedRenderX,$expectedRenderY]"
    }

    [FairyNativeProbe]::Focus($main.Handle) | Out-Null
    Start-Sleep -Milliseconds 200
    $foregroundBeforeHover = [FairyNativeProbe]::GetForegroundWindow()
    $scale = [Math]::Max(0.5, $render.Width / 640.0)
    $candidateAnchors = @(
        [pscustomobject]@{
            X = [int]$render.X + [int][Math]::Round(96 * $scale)
            Y = [int]$render.Y + [int][Math]::Round(130 * $scale)
        },
        [pscustomobject]@{
            X = [int]$render.X + [int]$render.Width - [int][Math]::Round(96 * $scale)
            Y = [int]$render.Y + [int][Math]::Round(130 * $scale)
        }
    )
    $activeAnchor = $null
    foreach ($candidate in $candidateAnchors) {
        Hold-Cursor $candidate.X $candidate.Y 900
        $input = Get-Window $process.Id "Fairy Presence Input"
        if ($null -ne $input -and $input.Visible) {
            $activeAnchor = $candidate
            break
        }
    }
    if ($null -eq $activeAnchor) {
        $debugOutput = & node $probeScript --port $port --expected-mode $ExpectedMode --duration-seconds 0
        $debugProbe = if ($LASTEXITCODE -eq 0) { ($debugOutput -join "`n") | ConvertFrom-Json } else { $null }
        $phase = if ($null -ne $debugProbe) { $debugProbe.final.interaction_phase } else { "unavailable" }
        $band = if ($null -ne $debugProbe) { $debugProbe.final.cursor_band } else { "unavailable" }
        $placement = if ($null -ne $debugProbe) { $debugProbe.final.placement | ConvertTo-Json -Compress } else { "unavailable" }
        $cursor = if ($null -ne $debugProbe) { $debugProbe.final.cursor_point | ConvertTo-Json -Compress } else { "unavailable" }
        $distance = if ($null -ne $debugProbe) { $debugProbe.final.cursor_distance } else { "unavailable" }
        throw "Hover did not reveal pet-input: phase=$phase, cursor_band=$band, cursor=$cursor, distance=$distance, placement=$placement, render=[$($render.X),$($render.Y),$($render.Width),$($render.Height)]"
    }
    if ([FairyNativeProbe]::GetForegroundWindow() -ne $foregroundBeforeHover) {
        throw "Hover stole keyboard focus"
    }
    $rootAtCore = [FairyNativeProbe]::RootWindowAt($activeAnchor.X, $activeAnchor.Y)
    if ($rootAtCore -eq $render.Handle) {
        throw "pet-render intercepted pointer hit testing"
    }

    Hold-Cursor ($main.X + 80) ($main.Y + 80) 2000
    $input = Get-Window $process.Id "Fairy Presence Input"
    if ($null -eq $input -or $input.Visible) {
        throw "pet-input did not return to its hidden passive state"
    }

    $probeOutput = & node $probeScript --port $port --expected-mode $ExpectedMode --duration-seconds 0
    if ($LASTEXITCODE -ne 0) { throw "WebView2 presence probe failed" }
    $webViewProbe = ($probeOutput -join "`n") | ConvertFrom-Json
    if ($webViewProbe.final.visible_controls -ne 0 -or
        $webViewProbe.final.horizontal_overflow -ne 0 -or
        $webViewProbe.final.vertical_overflow -ne 0) {
        throw "pet-render exposed controls or overflowed its native surface"
    }

    [FairyNativeProbe]::PostMessageW($main.Handle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
    Start-Sleep -Milliseconds 700
    $process.Refresh()
    if ($process.HasExited) { throw "Closing the main window terminated the tray application" }
    $render = Get-Window $process.Id "Fairy Presence Renderer"
    if ($null -eq $render -or -not $render.Visible) {
        throw "Pet did not remain available after the main window closed"
    }

    [PSCustomObject]@{
        process_id = $process.Id
        renderer_mode = $webViewProbe.final.renderer
        renderer_health = $webViewProbe.final.health
        gpu_preference = $GpuPreference
        gpu = $webViewProbe.gpu
        render_pass_through = $true
        hover_did_not_focus = $true
        input_returned_hidden = $true
        topmost_group = $true
        tray_survived_main_close = $true
        webview2_port = $port
    } | ConvertTo-Json -Depth 4
}
finally {
    if ($null -ne $process -and -not $KeepRunning) {
        $process.Refresh()
        if (-not $process.HasExited) {
            Stop-ProcessTree $process
        }
        $process.Dispose()
    }
    if (-not $KeepRunning) {
        Remove-VerifiedScratchDirectory $scratch $scratchPrefix
    }
    if ($GpuPreference -ne "auto") {
        if ($hadGpuPreference) {
            Set-ItemProperty -LiteralPath $gpuRegistryPath -Name $resolvedExecutable -Value $previousGpuPreference
        } else {
            Remove-ItemProperty -LiteralPath $gpuRegistryPath -Name $resolvedExecutable -ErrorAction SilentlyContinue
        }
    }
}
