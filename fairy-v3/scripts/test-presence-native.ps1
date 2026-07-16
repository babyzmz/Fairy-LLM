[CmdletBinding()]
param(
    [string]$Executable,
    [ValidateSet("liquid", "compatibility")]
    [string]$ExpectedMode = "liquid",
    [ValidateSet("auto", "power_saving", "high_performance")]
    [string]$GpuPreference = "auto",
    [int]$StartupTimeoutSeconds = 30,
    [switch]$VerifyRegressions,
    [switch]$FreshWebViewProfile,
    [switch]$KeepRunning
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Executable)) {
    $Executable = Join-Path $root "desktop\src-tauri\target\debug\fairy.exe"
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
    public string ClassName { get; set; }
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
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassNameW(IntPtr window, StringBuilder text, int capacity);
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
    private static extern bool SetWindowPos(
        IntPtr window,
        IntPtr insertAfter,
        int x,
        int y,
        int width,
        int height,
        uint flags
    );
    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")]
    private static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo);
    [DllImport("user32.dll")]
    public static extern bool PostMessageW(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")]
    private static extern int GetWindowRgn(IntPtr window, IntPtr region);
    [DllImport("gdi32.dll")]
    private static extern IntPtr CreateRectRgn(int left, int top, int right, int bottom);
    [DllImport("gdi32.dll")]
    private static extern bool PtInRegion(IntPtr region, int x, int y);
    [DllImport("gdi32.dll")]
    private static extern bool DeleteObject(IntPtr value);

    public static List<FairyWindowInfo> WindowsForProcess(int expectedProcessId) {
        var result = new List<FairyWindowInfo>();
        EnumWindows((window, _) => {
            uint processId;
            GetWindowThreadProcessId(window, out processId);
            if (processId != (uint)expectedProcessId) return true;
            var text = new StringBuilder(512);
            var className = new StringBuilder(256);
            GetWindowTextW(window, text, text.Capacity);
            GetClassNameW(window, className, className.Capacity);
            RECT rectangle;
            GetWindowRect(window, out rectangle);
            result.Add(new FairyWindowInfo {
                Handle = window,
                Title = text.ToString(),
                ClassName = className.ToString(),
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

    public static bool WindowRegionContainsCenter(FairyWindowInfo window) {
        var region = CreateRectRgn(0, 0, Math.Max(1, window.Width), Math.Max(1, window.Height));
        if (region == IntPtr.Zero) return false;
        try {
            if (GetWindowRgn(window.Handle, region) == 0) return false;
            return PtInRegion(region, window.Width / 2, window.Height / 2);
        }
        finally {
            DeleteObject(region);
        }
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

    public static bool MoveWithoutActivating(IntPtr window, int x, int y) {
        return SetWindowPos(window, IntPtr.Zero, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010);
    }

    public static void LeftButtonDown() {
        mouse_event(0x0002, 0, 0, 0, UIntPtr.Zero);
    }

    public static void LeftButtonUp() {
        mouse_event(0x0004, 0, 0, 0, UIntPtr.Zero);
    }

    public static bool Hide(IntPtr window) {
        return ShowWindow(window, 0);
    }

    public static bool ShowWithoutActivating(IntPtr window) {
        return ShowWindow(window, 4);
    }

    public static bool Minimize(IntPtr window) {
        return ShowWindow(window, 6);
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

function Get-LensWindows([int]$ProcessId) {
    return @(
        [FairyNativeProbe]::WindowsForProcess($ProcessId) |
            Where-Object ClassName -eq "FairyLensHost"
    )
}

function Wait-Window([int]$ProcessId, [string]$Title, [int]$TimeoutSeconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $window = Get-Window $ProcessId $Title
        if ($null -ne $window) { return $window }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    $available = [FairyNativeProbe]::WindowsForProcess($ProcessId) |
        ForEach-Object {
            "'$($_.Title)' class=$($_.ClassName) visible=$($_.Visible) rect=[$($_.X),$($_.Y),$($_.Width),$($_.Height)] hwnd=$($_.Handle)"
        }
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
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & taskkill.exe /PID $Target.Id /T /F 2>&1 | Out-Null
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $Target.Refresh()
    if ($Target.HasExited) { return }
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
    for ($attempt = 1; $attempt -le 20; $attempt += 1) {
        try {
            Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction Stop
            return
        }
        catch {
            if ($attempt -eq 20) {
                Write-Warning "Presence scratch cleanup is deferred because WebView2 still holds a crash dump: $resolved"
                return
            }
            Start-Sleep -Milliseconds 250
        }
    }
}

$scratchPrefix = "fairy-presence-native-"
$scratch = [System.IO.Path]::GetFullPath(
    (Join-Path ([System.IO.Path]::GetTempPath()) ($scratchPrefix + [guid]::NewGuid().ToString("N")))
)
$fairyData = Join-Path $scratch "FairyData"
$webViewData = Join-Path $scratch "WebView2"
New-Item -ItemType Directory -Force -Path $fairyData | Out-Null
if ($FreshWebViewProfile) {
    New-Item -ItemType Directory -Force -Path $webViewData | Out-Null
}
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
    $webViewArguments = "--remote-debugging-port=$port"
    if ($GpuPreference -eq "power_saving") {
        $webViewArguments += " --force_low_power_gpu"
    } elseif ($GpuPreference -eq "high_performance") {
        $webViewArguments += " --force_high_performance_gpu"
    }
    if ($ExpectedMode -eq "compatibility") {
        $webViewArguments += " --disable-gpu --disable-software-rasterizer"
    }
    $childEnvironment = @{
        FAIRY_DESKTOP_DATA_DIR = $fairyData
        WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = $webViewArguments
    }
    if ($FreshWebViewProfile) {
        $childEnvironment.WEBVIEW2_USER_DATA_FOLDER = $webViewData
    } else {
        $childEnvironment.WEBVIEW2_USER_DATA_FOLDER = $null
    }
    if ([string]::IsNullOrWhiteSpace($env:FAIRY_CORE_ROOT) -and
        $resolvedExecutable -like "*\target\debug\fairy.exe") {
        $childEnvironment.FAIRY_CORE_ROOT = (Resolve-Path -LiteralPath (Join-Path $root "core")).Path
    }
    $previousChildEnvironment = @{}
    try {
        foreach ($entry in $childEnvironment.GetEnumerator()) {
            $previousChildEnvironment[$entry.Key] = [Environment]::GetEnvironmentVariable(
                $entry.Key,
                [EnvironmentVariableTarget]::Process
            )
            $nextValue = if ($null -eq $entry.Value) { $null } else { [string]$entry.Value }
            [Environment]::SetEnvironmentVariable(
                $entry.Key,
                $nextValue,
                [EnvironmentVariableTarget]::Process
            )
        }
        $process = [System.Diagnostics.Process]::Start($startInfo)
    }
    finally {
        foreach ($entry in $previousChildEnvironment.GetEnumerator()) {
            [Environment]::SetEnvironmentVariable(
                $entry.Key,
                $entry.Value,
                [EnvironmentVariableTarget]::Process
            )
        }
    }
    if ($null -eq $process) { throw "Fairy process did not start" }

    $main = Wait-Window $process.Id "Fairy" $StartupTimeoutSeconds
    $render = Wait-Window $process.Id "Fairy Presence Renderer" $StartupTimeoutSeconds
    $inputWindow = Wait-Window $process.Id "Fairy Presence Input" $StartupTimeoutSeconds
    $cursorInjectionAvailable = [FairyNativeProbe]::SetCursorPos(($main.X + 80), ($main.Y + 80))
    if ($cursorInjectionAvailable) {
        Hold-Cursor ($main.X + 80) ($main.Y + 80) 2000
    } else {
        Start-Sleep -Milliseconds 2000
    }
    $main = Get-Window $process.Id "Fairy"
    $render = Get-Window $process.Id "Fairy Presence Renderer"
    $inputWindow = Get-Window $process.Id "Fairy Presence Input"
    if ($null -eq $main -or $null -eq $render -or $null -eq $inputWindow) {
        $process.Refresh()
        throw "A required native window disappeared during startup: main=$($null -ne $main), render=$($null -ne $render), input=$($null -ne $inputWindow), process_exited=$($process.HasExited)"
    }
    if (-not $main.Visible -or -not $render.Visible -or -not $inputWindow.Visible) {
        throw "Unexpected passive Fairy visibility: main=$($main.Visible), render=$($render.Visible), input=$($inputWindow.Visible)"
    }
    $initialScale = [Math]::Max(0.5, $render.Width / 640.0)
    $expectedCoreExtent = [int][Math]::Round(144 * $initialScale)
    if ([Math]::Abs($inputWindow.Width - $expectedCoreExtent) -gt 2 -or
        [Math]::Abs($inputWindow.Height - $expectedCoreExtent) -gt 2) {
        throw "Passive pet-input is not the core interaction proxy: size=[$($inputWindow.Width),$($inputWindow.Height)], expected=$expectedCoreExtent"
    }
    $legacyLenses = @(Get-LensWindows $process.Id)
    if ($legacyLenses.Count -gt 0) {
        throw "PRESENCE_DOM_REFRACTION_RISK: a legacy Magnifier surface can duplicate input text"
    }

    $WS_EX_TOPMOST = 0x00000008L
    $WS_EX_NOACTIVATE = 0x08000000L
    foreach ($mask in @($WS_EX_TOPMOST, $WS_EX_NOACTIVATE)) {
        if (-not (Test-Style $render.ExtendedStyle $mask)) {
            throw ("pet-render is missing extended style 0x{0:X}" -f $mask)
        }
    }
    if (-not (Test-Style $inputWindow.ExtendedStyle $WS_EX_TOPMOST)) {
        throw ("pet-input did not start passive and topmost: extended_style=0x{0:X}" -f $inputWindow.ExtendedStyle)
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
    $hoverDidNotFocus = $null
    if ($cursorInjectionAvailable) {
        foreach ($candidate in $candidateAnchors) {
            Hold-Cursor $candidate.X $candidate.Y 900
            $inputWindow = Get-Window $process.Id "Fairy Presence Input"
            if ($null -ne $inputWindow -and $inputWindow.Visible -and $inputWindow.Width -gt $expectedCoreExtent) {
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
        $hoverDidNotFocus = [FairyNativeProbe]::GetForegroundWindow() -eq $foregroundBeforeHover
        if (-not $hoverDidNotFocus) {
            throw "Hover stole keyboard focus"
        }
    } else {
        $activeAnchor = $candidateAnchors[0]
        $inputProbeScript = Join-Path $root "desktop\scripts\probe-presence-input.mjs"
        $openInputOutput = & node $inputProbeScript --port $port --action open
        if ($LASTEXITCODE -ne 0) { throw "WebView2 could not open pet-input for native regression checks" }
        $openInput = ($openInputOutput -join "`n") | ConvertFrom-Json
        if (-not $openInput.input_open -or $openInput.layout -ne "compact") {
            throw "WebView2 did not establish the compact pet-input layout"
        }
        Start-Sleep -Milliseconds 250
    }
    $rootAtCore = [FairyNativeProbe]::RootWindowAt($activeAnchor.X, $activeAnchor.Y)
    if ($rootAtCore -eq $render.Handle) {
        throw "pet-render intercepted pointer hit testing"
    }
    $activeLenses = @(Get-LensWindows $process.Id)
    if ($activeLenses.Count -gt 0) {
        throw "PRESENCE_DOM_REFRACTION_RISK: compact input created a legacy Magnifier surface"
    }

    $inputProbe = $null
    $menuProbe = $null
    $renderDeltaX = $null
    $renderDeltaY = $null
    $inputDeltaX = $null
    $inputDeltaY = $null
    $renderLifecycleIndependent = $null
    if ($VerifyRegressions) {
        $inputProbeScript = Join-Path $root "desktop\scripts\probe-presence-input.mjs"
        $menuProbeOutput = & node $inputProbeScript --port $port --action menu
        if ($LASTEXITCODE -ne 0) { throw "WebView2 companion menu regression probe failed" }
        $menuProbe = ($menuProbeOutput -join "`n") | ConvertFrom-Json
        $requiredMenuItems = @("New chat", "Open Fairy", "Settings", "Move Fairy", "Reset position", "Exit Fairy")
        $missingMenuItems = @($requiredMenuItems | Where-Object { $_ -notin $menuProbe.items })
        if (-not $menuProbe.visible -or $missingMenuItems.Count -gt 0) {
            throw "PRESENCE_CONTEXT_MENU_UNAVAILABLE: companion context menu is incomplete"
        }
        $openInputOutput = & node $inputProbeScript --port $port --action open
        if ($LASTEXITCODE -ne 0) { throw "WebView2 could not reopen pet-input after menu probe" }
        $compactDeadline = [DateTime]::UtcNow.AddSeconds(5)
        do {
            $compactInput = Get-Window $process.Id "Fairy Presence Input"
            if ($null -ne $compactInput -and $compactInput.Width -gt ($expectedCoreExtent + 20)) {
                break
            }
            Start-Sleep -Milliseconds 50
        } while ([DateTime]::UtcNow -lt $compactDeadline)
        if ($null -eq $compactInput -or $compactInput.Width -le ($expectedCoreExtent + 20)) {
            throw "pet-input did not reach its compact native layout after the menu closed"
        }
        Start-Sleep -Milliseconds 300
        $renderBefore = Get-Window $process.Id "Fairy Presence Renderer"
        $inputBefore = Get-Window $process.Id "Fairy Presence Input"
        if ($null -eq $renderBefore -or $null -eq $inputBefore -or -not $inputBefore.Visible) {
            throw "Native regression probe requires visible render and input windows"
        }
        $inputProbeOutput = & node $inputProbeScript --port $port --action measure
        if ($LASTEXITCODE -ne 0) { throw "WebView2 input regression probe failed" }
        $inputProbe = ($inputProbeOutput -join "`n") | ConvertFrom-Json
        $dpr = [double]$inputProbe.device_pixel_ratio
        $dragStartX = $inputBefore.X + [int][Math]::Round(($inputProbe.grip.left + ($inputProbe.grip.width / 2)) * $dpr)
        $dragStartY = $inputBefore.Y + [int][Math]::Round(($inputProbe.grip.top + ($inputProbe.grip.height / 2)) * $dpr)
        if (-not [FairyNativeProbe]::SetCursorPos($dragStartX, $dragStartY)) {
            throw "Windows rejected the native drag start position"
        }
        [FairyNativeProbe]::LeftButtonDown()
        try {
            Start-Sleep -Milliseconds 80
            foreach ($step in 1..6) {
                $x = $dragStartX + [int][Math]::Round(-48 * ($step / 6.0))
                if (-not [FairyNativeProbe]::SetCursorPos($x, $dragStartY)) {
                    throw "Windows rejected a native drag position"
                }
                Start-Sleep -Milliseconds 35
            }
        }
        finally {
            [FairyNativeProbe]::LeftButtonUp()
        }
        Start-Sleep -Milliseconds 250
        $renderAfter = Get-Window $process.Id "Fairy Presence Renderer"
        $inputAfter = Get-Window $process.Id "Fairy Presence Input"
        if ($null -eq $renderAfter -or $null -eq $inputAfter) {
            throw "A native Presence window disappeared during the regression probe"
        }
        $renderDeltaX = $renderAfter.X - $renderBefore.X
        $renderDeltaY = $renderAfter.Y - $renderBefore.Y
        $inputDeltaX = $inputAfter.X - $inputBefore.X
        $inputDeltaY = $inputAfter.Y - $inputBefore.Y
        if ([Math]::Abs($renderDeltaX - $inputDeltaX) -gt 2 -or
            [Math]::Abs($renderDeltaY - $inputDeltaY) -gt 2) {
            throw "PRESENCE_GROUP_DRAG_DESYNCHRONIZED: render_delta=[$renderDeltaX,$renderDeltaY], input_delta=[$inputDeltaX,$inputDeltaY], geometry=$($inputProbe | ConvertTo-Json -Compress -Depth 5)"
        }
        if ([Math]::Abs($inputDeltaX) -lt 40 -and [Math]::Abs($inputDeltaY) -lt 40) {
            throw "PRESENCE_GROUP_DRAG_INERT: Fairy and input did not move; render_delta=[$renderDeltaX,$renderDeltaY], input_delta=[$inputDeltaX,$inputDeltaY]"
        }
        $maximumEdgeDelta = @(
            [double]$inputProbe.edge_delta_physical.left,
            [double]$inputProbe.edge_delta_physical.top,
            [double]$inputProbe.edge_delta_physical.right,
            [double]$inputProbe.edge_delta_physical.bottom
        ) | Measure-Object -Maximum | Select-Object -ExpandProperty Maximum
        if ($maximumEdgeDelta -gt 2) {
            throw "PRESENCE_INPUT_GEOMETRY_MISMATCH: visible shell and textarea differ by more than 2 physical pixels; geometry=$($inputProbe | ConvertTo-Json -Compress -Depth 5)"
        }
        $failedFocusPoints = @($inputProbe.focus_points | Where-Object { -not $_.focused })
        if ($failedFocusPoints.Count -gt 0) {
            throw "PRESENCE_INPUT_HIT_TARGET_MISMATCH: textarea did not focus at $($failedFocusPoints.name -join ', ')"
        }

        $inputBeforeRenderMove = Get-Window $process.Id "Fairy Presence Input"
        if (-not [FairyNativeProbe]::MoveWithoutActivating(
            $renderAfter.Handle,
            ($renderAfter.X - 32),
            $renderAfter.Y
        )) {
            throw "Windows rejected the independent pet-render move"
        }
        Start-Sleep -Milliseconds 450
        $inputAfterRenderMove = Get-Window $process.Id "Fairy Presence Input"
        if ($null -eq $inputAfterRenderMove -or
            $inputAfterRenderMove.X -ne $inputBeforeRenderMove.X -or
            $inputAfterRenderMove.Y -ne $inputBeforeRenderMove.Y) {
            throw "BRIDGE_OFF_WINDOW_COUPLING: pet-render movement changed pet-input coordinates"
        }
        $renderMoved = Get-Window $process.Id "Fairy Presence Renderer"
        [FairyNativeProbe]::Hide($renderMoved.Handle) | Out-Null
        Start-Sleep -Milliseconds 250
        $inputWhileRenderHidden = Get-Window $process.Id "Fairy Presence Input"
        if ($null -eq $inputWhileRenderHidden -or -not $inputWhileRenderHidden.Visible -or
            $inputWhileRenderHidden.X -ne $inputAfterRenderMove.X -or
            $inputWhileRenderHidden.Y -ne $inputAfterRenderMove.Y) {
            throw "BRIDGE_OFF_WINDOW_LIFECYCLE_COUPLING: hiding pet-render changed pet-input"
        }
        [FairyNativeProbe]::ShowWithoutActivating($renderMoved.Handle) | Out-Null
        Start-Sleep -Milliseconds 250
        $renderRestored = Get-Window $process.Id "Fairy Presence Renderer"
        [FairyNativeProbe]::Minimize($renderRestored.Handle) | Out-Null
        Start-Sleep -Milliseconds 250
        $inputWhileRenderMinimized = Get-Window $process.Id "Fairy Presence Input"
        if ($null -eq $inputWhileRenderMinimized -or -not $inputWhileRenderMinimized.Visible -or
            $inputWhileRenderMinimized.X -ne $inputAfterRenderMove.X -or
            $inputWhileRenderMinimized.Y -ne $inputAfterRenderMove.Y) {
            throw "BRIDGE_OFF_WINDOW_LIFECYCLE_COUPLING: minimizing pet-render changed pet-input"
        }
        [FairyNativeProbe]::ShowWithoutActivating($renderRestored.Handle) | Out-Null
        $renderLifecycleIndependent = $true
    }

    if ($cursorInjectionAvailable) {
        Hold-Cursor ($main.X + 80) ($main.Y + 80) 2000
    }
    $inputProbeScript = Join-Path $root "desktop\scripts\probe-presence-input.mjs"
    $closeInputOutput = & node $inputProbeScript --port $port --action close
    if ($LASTEXITCODE -ne 0) { throw "WebView2 could not close pet-input after native regression checks" }
    Start-Sleep -Milliseconds 500
    $inputWindow = Get-Window $process.Id "Fairy Presence Input"
    if ($null -eq $inputWindow -or -not $inputWindow.Visible -or
        [Math]::Abs($inputWindow.Width - $expectedCoreExtent) -gt 2 -or
        [Math]::Abs($inputWindow.Height - $expectedCoreExtent) -gt 2) {
        throw "pet-input did not return to its passive core interaction proxy"
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
    $inputCloseIndependent = $null
    if ($VerifyRegressions) {
        $inputBeforeClose = Get-Window $process.Id "Fairy Presence Input"
        $renderBeforeInputClose = Get-Window $process.Id "Fairy Presence Renderer"
        [FairyNativeProbe]::PostMessageW(
            $inputBeforeClose.Handle,
            0x0010,
            [IntPtr]::Zero,
            [IntPtr]::Zero
        ) | Out-Null
        Start-Sleep -Milliseconds 350
        $renderAfterInputClose = Get-Window $process.Id "Fairy Presence Renderer"
        if ($null -eq $renderAfterInputClose -or -not $renderAfterInputClose.Visible -or
            $renderAfterInputClose.X -ne $renderBeforeInputClose.X -or
            $renderAfterInputClose.Y -ne $renderBeforeInputClose.Y) {
            throw "BRIDGE_OFF_WINDOW_LIFECYCLE_COUPLING: closing pet-input changed pet-render"
        }
        $inputCloseIndependent = $true
    }

    [PSCustomObject]@{
        process_id = $process.Id
        renderer_mode = $webViewProbe.final.renderer
        renderer_health = $webViewProbe.final.health
        gpu_preference = $GpuPreference
        gpu = $webViewProbe.gpu
        render_pass_through = $true
        hover_did_not_focus = $hoverDidNotFocus
        cursor_injection_available = $cursorInjectionAvailable
        input_returned_to_core_proxy = $true
        topmost_group = $true
        tray_survived_main_close = $true
        input_core_proxy_visible_at_rest = $true
        legacy_magnifier_surfaces = 0
        input_device_pixel_ratio = if ($VerifyRegressions) { $inputProbe.device_pixel_ratio } else { $null }
        input_edge_delta_physical = if ($VerifyRegressions) { $inputProbe.edge_delta_physical } else { $null }
        input_focus_points = if ($VerifyRegressions) { $inputProbe.focus_points } else { @() }
        input_drag_delta = if ($VerifyRegressions) { @($inputDeltaX, $inputDeltaY) } else { $null }
        render_group_drag_delta = if ($VerifyRegressions) { @($renderDeltaX, $renderDeltaY) } else { $null }
        render_lifecycle_independent = $renderLifecycleIndependent
        input_close_independent = $inputCloseIndependent
        context_menu_items = if ($VerifyRegressions) { $menuProbe.items } else { @() }
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
