[CmdletBinding()]
param(
    [string]$Executable,
    [ValidateSet(60, 144)]
    [int]$TargetFps = 60,
    [ValidateRange(1, 300)]
    [int]$DurationSeconds = 10,
    [string]$OutputPath,
    [string]$ScreenshotPath,
    [switch]$DiagnosticSolid,
    [switch]$KeepRunning
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Executable)) {
    $Executable = Join-Path $root "desktop\src-tauri\target\debug\fairy.exe"
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $root ".tmp\presence-native-gpu-$TargetFps.json"
}
if ([string]::IsNullOrWhiteSpace($ScreenshotPath)) {
    $ScreenshotPath = Join-Path $root ".tmp\presence-native-gpu-$TargetFps.png"
}

function Test-NativeExecutableStale([string]$BinaryPath) {
    if (-not (Test-Path -LiteralPath $BinaryPath -PathType Leaf)) { return $true }
    $binaryWrite = (Get-Item -LiteralPath $BinaryPath).LastWriteTimeUtc
    $manifestRoot = Join-Path $root "desktop\src-tauri"
    $inputs = @(
        (Get-Item -LiteralPath (Join-Path $manifestRoot "Cargo.toml")),
        (Get-Item -LiteralPath (Join-Path $manifestRoot "Cargo.lock"))
    )
    $inputs += Get-ChildItem -LiteralPath (Join-Path $manifestRoot "src") -Recurse -File |
        Where-Object Extension -in ".rs", ".hlsl"
    return ($inputs | Where-Object LastWriteTimeUtc -gt $binaryWrite | Select-Object -First 1) -ne $null
}

if (Test-NativeExecutableStale $Executable) {
    & cargo build --manifest-path (Join-Path $root "desktop\src-tauri\Cargo.toml")
    if ($LASTEXITCODE -ne 0) { throw "Fairy debug build failed" }
}
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Fairy executable is missing after debug build: $Executable"
}

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public sealed class FairyNativeGpuWindow {
    public IntPtr Handle { get; set; }
    public string Title { get; set; }
    public int X { get; set; }
    public int Y { get; set; }
    public int Width { get; set; }
    public int Height { get; set; }
    public long ExtendedStyle { get; set; }
}

public static class FairyNativeGpuProbe {
    private delegate bool EnumWindowsCallback(IntPtr window, IntPtr parameter);
    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsCallback callback, IntPtr parameter);
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextW(IntPtr window, StringBuilder text, int capacity);
    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr window, out RECT rectangle);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")]
    private static extern IntPtr GetWindowLongPtr(IntPtr window, int index);
    [DllImport("user32.dll")]
    private static extern IntPtr SendMessageW(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

    public static List<FairyNativeGpuWindow> WindowsForProcess(int expectedProcessId) {
        var result = new List<FairyNativeGpuWindow>();
        EnumWindows((window, _) => {
            uint processId;
            GetWindowThreadProcessId(window, out processId);
            if (processId != (uint)expectedProcessId) return true;
            var text = new StringBuilder(256);
            GetWindowTextW(window, text, text.Capacity);
            RECT rectangle;
            GetWindowRect(window, out rectangle);
            result.Add(new FairyNativeGpuWindow {
                Handle = window,
                Title = text.ToString(),
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

    public static bool ReturnsTransparentHitTest(FairyNativeGpuWindow window) {
        long packed = ((long)(window.Y + window.Height / 2) << 16) |
            (uint)(window.X + window.Width / 2) & 0xFFFF;
        return SendMessageW(window.Handle, 0x0084, IntPtr.Zero, new IntPtr(packed)).ToInt64() == -1;
    }
}
'@

function Get-AvailablePort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port }
    finally { $listener.Stop() }
}

function Wait-FairyWindow([int]$ProcessId, [string]$Title, [int]$TimeoutSeconds = 30) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $window = [FairyNativeGpuProbe]::WindowsForProcess($ProcessId) |
            Where-Object Title -eq $Title |
            Select-Object -First 1
        if ($null -ne $window -and $window.Width -gt 0 -and $window.Height -gt 0) { return $window }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for native window: $Title"
}

function Stop-ProcessTree([System.Diagnostics.Process]$Target) {
    $Target.Refresh()
    if ($Target.HasExited) { return }
    Stop-Process -Id $Target.Id -Force -ErrorAction SilentlyContinue
    $Target.WaitForExit(5000) | Out-Null
}

function Remove-VerifiedScratch([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not ([System.IO.Path]::GetFileName($resolved)).StartsWith("fairy-native-gpu-", [StringComparison]::Ordinal)) {
        throw "Refusing to remove unverified scratch path: $resolved"
    }
    $item = Get-Item -Force -LiteralPath $resolved
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to remove reparse-point scratch path: $resolved"
    }
    for ($attempt = 1; $attempt -le 20; $attempt += 1) {
        try {
            Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction Stop
            return
        }
        catch {
            if ($attempt -eq 20) {
                Write-Warning "Native GPU scratch cleanup is deferred: $resolved"
                return
            }
            Start-Sleep -Milliseconds 250
        }
    }
}

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("fairy-native-gpu-" + [guid]::NewGuid().ToString("N"))
$fairyData = Join-Path $scratch "FairyData"
$webViewData = Join-Path $scratch "WebView2"
$preferencesDirectory = Join-Path $fairyData "preferences"
New-Item -ItemType Directory -Force -Path $preferencesDirectory, $webViewData | Out-Null
$preferences = [ordered]@{
    schema_version = 4; revision = 0; language = "system"; launch_at_startup = $false
    minimize_to_tray = $true; theme = "system"; reduced_motion = $false; compact_density = $false
    selected_profile_id = $null; voice_auto_play_chat = $false; voice_auto_play_pet = $false
    voice_volume_percent = 80; voice_rate_percent = 100; permission_cloud_profile = "standard"
    memory_enabled = $true; memory_retention_days = 90; analytics_enabled = $false
    pet_enabled = $true; pet_always_on_top = $true; pet_muted = $true; pet_size_percent = 100
    pet_opacity_percent = 92; pet_motion_enabled = $true; pet_particles_enabled = $true
    pet_hover_enabled = $true; pet_hover_dwell_ms = 250; pet_do_not_disturb = $false
    pet_remember_position = $true; pet_renderer_mode = "auto"; pet_optics_mode = "enhanced"
    pet_target_fps = $TargetFps; pet_anchor = $null; developer_mode = $false
}
[System.IO.File]::WriteAllText(
    (Join-Path $preferencesDirectory "desktop.json"),
    ($preferences | ConvertTo-Json -Depth 4),
    [System.Text.UTF8Encoding]::new($false)
)

$port = Get-AvailablePort
$process = $null
$probe = Join-Path $root "desktop\scripts\probe-presence-native-gpu.mjs"
try {
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = (Resolve-Path -LiteralPath $Executable).Path
    $startInfo.WorkingDirectory = Split-Path -Parent $startInfo.FileName
    $startInfo.UseShellExecute = $false
    $startInfo.Environment["FAIRY_DESKTOP_DATA_DIR"] = $fairyData
    $startInfo.Environment["WEBVIEW2_USER_DATA_FOLDER"] = $webViewData
    $startInfo.Environment["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--remote-debugging-port=$port --force_high_performance_gpu"
    $startInfo.Environment["FAIRY_CORE_ROOT"] = (Resolve-Path -LiteralPath (Join-Path $root "core")).Path
    if ($DiagnosticSolid) {
        $startInfo.Environment["FAIRY_PRESENCE_GPU_DIAGNOSTIC_SOLID"] = "1"
    }
    $process = [System.Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) { throw "Fairy process did not start" }
    $render = Wait-FairyWindow $process.Id "Fairy Presence Renderer"
    $raw = & node $probe --port $port --action run --target-fps $TargetFps --duration-seconds $DurationSeconds
    if ($LASTEXITCODE -ne 0) { throw "Native GPU probe failed" }
    $result = ($raw -join "`n") | ConvertFrom-Json
    $status = $result.status
    if ($status.lifecycle -ne "running" -or -not $status.zero_copy_capture -or $status.pixel_ipc) {
        throw "Native GPU contract failed: $($status | ConvertTo-Json -Compress)"
    }
    if ([int]$status.target_frame_rate -ne $TargetFps) {
        throw "Native GPU renderer reported the wrong target frame rate: $($status.target_frame_rate)"
    }
    $minimumFrames = [Math]::Max(1, [Math]::Floor($DurationSeconds * $TargetFps * 0.75))
    if ([long]$status.frames_presented -lt $minimumFrames) {
        throw "Native GPU renderer presented too few frames: $($status.frames_presented)"
    }
    if ([double]$status.capture_fps_avg -lt ($TargetFps * 0.90)) {
        throw "Native GPU average frame rate missed the 90% gate: $($status.capture_fps_avg)"
    }
    if ([double]$status.frame_interval_p1_fps -lt ($TargetFps * 0.70)) {
        throw "Native GPU P1 frame rate missed the 70% gate: $($status.frame_interval_p1_fps)"
    }
    if ([double]$status.callback_to_present_p95_ms -gt 8.0 -or [double]$status.present_p95_ms -gt 8.0) {
        throw "Native GPU p95 render latency exceeded 8ms"
    }
    & node $probe --port $port --action prepare-capture --target-fps $TargetFps --duration-seconds 0 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Native GPU visual capture preparation failed" }
    $render = Wait-FairyWindow $process.Id "Fairy Native Presence Renderer"
    & node $probe --port $port --action capture --target-fps $TargetFps --duration-seconds 0 --output $ScreenshotPath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Native GPU WGC screenshot failed" }
    if (-not (Test-Path -LiteralPath $ScreenshotPath -PathType Leaf) -or
        (Get-Item -LiteralPath $ScreenshotPath).Length -lt 1KB) {
        throw "Native GPU WGC screenshot is missing or empty"
    }
    $passThrough = [FairyNativeGpuProbe]::ReturnsTransparentHitTest($render)
    if (-not $passThrough) { throw "pet-render did not preserve native click-through" }
    $report = [ordered]@{
        generated_at = [DateTime]::UtcNow.ToString("o")
        target_fps = $TargetFps
        duration_seconds = $DurationSeconds
        status = $status
        render_window = $render
        click_through = $passThrough
        screenshot = [System.IO.Path]::GetFullPath($ScreenshotPath)
    }
    $outputDirectory = Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath))
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8
    $report | ConvertTo-Json -Depth 8
    if (-not $KeepRunning) {
        & node $probe --port $port --action stop --target-fps $TargetFps --duration-seconds 0 | Out-Null
    }
}
finally {
    if (-not $KeepRunning -and $null -ne $process) { Stop-ProcessTree $process }
    if (-not $KeepRunning) {
        Start-Sleep -Milliseconds 300
        Remove-VerifiedScratch $scratch
    }
}
