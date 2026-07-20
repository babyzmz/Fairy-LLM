[CmdletBinding()]
param(
    [string]$Executable,
    [ValidateRange(0.1, 480.0)]
    [double]$DurationMinutes = 30,
    [ValidateRange(1, 60)]
    [int]$SampleSeconds = 5,
    [ValidateRange(0, 300)]
    [int]$WarmupSeconds = 30,
    [ValidateRange(1, 100)]
    [double]$MaximumMemoryGrowthMb = 10,
    [ValidateRange(0.1, 100)]
    [double]$MaximumSingleCoreCpuPercent = 5,
    [ValidateSet("auto", "power_saving", "high_performance")]
    [string]$GpuPreference = "auto",
    [string]$ExpectedGpuPattern,
    [ValidateSet("normal", "static-backdrop", "capture-only", "ipc-upload-only", "single-renderer", "no-particles", "no-refraction")]
    [string]$ExperimentMode = "normal",
    [ValidateSet(60, 144, 300)]
    [int]$TargetFps = 60,
    [ValidateSet("standard", "enhanced")]
    [string]$OpticsMode = "standard",
    [switch]$KeepPresenceActive,
    [switch]$SkipRendererProbe,
    [string]$OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Executable)) {
    $Executable = Join-Path $root "desktop\src-tauri\target\release\fairy.exe"
}

if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Fairy executable is missing: $Executable"
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path ([System.IO.Path]::GetTempPath()) "fairy-presence-soak.json"
}
$outputDirectory = Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath))
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

if ($KeepPresenceActive -and -not ("FairyPresenceSoakCursor" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class FairyPresenceSoakCursor {
    private delegate bool EnumWindowsCallback(IntPtr window, IntPtr parameter);
    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsCallback callback, IntPtr parameter);
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassNameW(IntPtr window, StringBuilder text, int capacity);
    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr window, out RECT rectangle);
    [DllImport("user32.dll")]
    private static extern bool SetCursorPos(int x, int y);

    public static bool MoveToRenderWindow(int expectedProcessId) {
        var moved = false;
        EnumWindows((window, _) => {
            uint processId;
            GetWindowThreadProcessId(window, out processId);
            if (processId != (uint)expectedProcessId) return true;
            var className = new StringBuilder(256);
            GetClassNameW(window, className, className.Capacity);
            if (className.ToString() != "FairyNativePresenceHitProxyClass") return true;
            RECT rectangle;
            if (!GetWindowRect(window, out rectangle)) return false;
            moved = SetCursorPos(
                rectangle.Left + Math.Min(120, Math.Max(1, rectangle.Right - rectangle.Left) / 2),
                rectangle.Top + Math.Min(130, Math.Max(1, rectangle.Bottom - rectangle.Top) / 2)
            );
            return false;
        }, IntPtr.Zero);
        return moved;
    }
}
'@
}

function Get-AvailablePort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port }
    finally { $listener.Stop() }
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
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

function Get-ProcessTreeSnapshot([int]$RootProcessId) {
    $processRows = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId)
    $treeIds = [System.Collections.Generic.HashSet[int]]::new()
    [void]$treeIds.Add($RootProcessId)
    do {
        $added = $false
        foreach ($row in $processRows) {
            $processId = [int]$row.ProcessId
            if ($treeIds.Contains([int]$row.ParentProcessId) -and $treeIds.Add($processId)) {
                $added = $true
            }
        }
    } while ($added)

    $cpuByProcess = @{}
    $presenceCpuByProcess = @{}
    $presenceProcesses = [System.Collections.Generic.List[object]]::new()
    [long]$privateBytes = 0
    [long]$workingSetBytes = 0
    [long]$presencePrivateBytes = 0
    [long]$presenceWorkingSetBytes = 0
    foreach ($processId in $treeIds) {
        $tracked = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -eq $tracked) { continue }
        $cpuByProcess[[string]$processId] = $tracked.TotalProcessorTime.TotalSeconds
        $privateBytes += $tracked.PrivateMemorySize64
        $workingSetBytes += $tracked.WorkingSet64
        if ($processId -eq $RootProcessId -or $tracked.ProcessName -eq "msedgewebview2") {
            $presenceCpuByProcess[[string]$processId] = $tracked.TotalProcessorTime.TotalSeconds
            $presencePrivateBytes += $tracked.PrivateMemorySize64
            $presenceWorkingSetBytes += $tracked.WorkingSet64
            $presenceProcesses.Add([PSCustomObject]@{
                process_id = $processId
                process_name = $tracked.ProcessName
                private_bytes = $tracked.PrivateMemorySize64
                working_set_bytes = $tracked.WorkingSet64
            })
        }
        $tracked.Dispose()
    }
    return [PSCustomObject]@{
        process_count = $cpuByProcess.Count
        cpu_by_process = $cpuByProcess
        private_bytes = $privateBytes
        working_set_bytes = $workingSetBytes
        presence_process_count = $presenceCpuByProcess.Count
        presence_cpu_by_process = $presenceCpuByProcess
        presence_processes = $presenceProcesses
        presence_private_bytes = $presencePrivateBytes
        presence_working_set_bytes = $presenceWorkingSetBytes
    }
}

function Get-ProcessTreeCpuDelta($Previous, $Current) {
    [double]$delta = 0
    foreach ($entry in $Current.GetEnumerator()) {
        $before = if ($Previous.ContainsKey($entry.Key)) { [double]$Previous[$entry.Key] } else { 0 }
        $delta += [Math]::Max(0, [double]$entry.Value - $before)
    }
    return $delta
}

$scratchPrefix = "fairy-presence-soak-"
$scratch = [System.IO.Path]::GetFullPath(
    (Join-Path ([System.IO.Path]::GetTempPath()) ($scratchPrefix + [guid]::NewGuid().ToString("N")))
)
$localAppData = Join-Path $scratch "LocalAppData"
$appData = Join-Path $scratch "AppData"
$fairyData = Join-Path $scratch "FairyData"
$webViewData = Join-Path $scratch "WebView2"
$rendererOutput = Join-Path $scratch "renderer.json"
New-Item -ItemType Directory -Force -Path $localAppData, $appData, $fairyData, $webViewData | Out-Null
$preferencesDirectory = Join-Path $fairyData "preferences"
$preferencesPath = Join-Path $preferencesDirectory "desktop.json"
New-Item -ItemType Directory -Force -Path $preferencesDirectory | Out-Null
$preferences = [ordered]@{
    schema_version = 4
    revision = 0
    language = "system"
    launch_at_startup = $false
    minimize_to_tray = $true
    theme = "system"
    reduced_motion = $false
    compact_density = $false
    selected_profile_id = $null
    voice_auto_play_chat = $false
    voice_auto_play_pet = $true
    voice_volume_percent = 80
    voice_rate_percent = 100
    permission_cloud_profile = "standard"
    memory_enabled = $true
    memory_retention_days = 90
    analytics_enabled = $false
    pet_enabled = $true
    pet_always_on_top = $true
    pet_muted = $false
    pet_size_percent = 100
    pet_opacity_percent = 92
    pet_motion_enabled = $true
    pet_particles_enabled = $true
    pet_hover_enabled = $true
    pet_hover_dwell_ms = 250
    pet_do_not_disturb = $false
    pet_remember_position = $true
    pet_renderer_mode = "auto"
    pet_optics_mode = $OpticsMode
    pet_target_fps = $TargetFps
    pet_anchor = $null
    developer_mode = $false
}
$preferencesJson = $preferences | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText(
    $preferencesPath,
    $preferencesJson,
    [System.Text.UTF8Encoding]::new($false)
)
$port = Get-AvailablePort
$durationSeconds = [int][Math]::Round($DurationMinutes * 60)
$process = $null
$probe = $null

try {
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = (Resolve-Path -LiteralPath $Executable).Path
    $startInfo.WorkingDirectory = Split-Path -Parent $startInfo.FileName
    $startInfo.UseShellExecute = $false
    $startInfo.Environment["LOCALAPPDATA"] = $localAppData
    $startInfo.Environment["APPDATA"] = $appData
    $startInfo.Environment["FAIRY_DESKTOP_DATA_DIR"] = $fairyData
    $startInfo.Environment["WEBVIEW2_USER_DATA_FOLDER"] = $webViewData
    $webViewArguments = @()
    if (-not $SkipRendererProbe) {
        $webViewArguments += "--remote-debugging-port=$port"
    }
    if ($GpuPreference -eq "power_saving") {
        $webViewArguments += "--force_low_power_gpu"
    } elseif ($GpuPreference -eq "high_performance") {
        $webViewArguments += "--force_high_performance_gpu"
    }
    if ($webViewArguments.Count -gt 0) {
        $startInfo.Environment["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = $webViewArguments -join " "
    }
    $process = [System.Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) { throw "Fairy process did not start" }

    if ($KeepPresenceActive) {
        $activeDeadline = [DateTime]::UtcNow.AddSeconds(30)
        while (-not [FairyPresenceSoakCursor]::MoveToRenderWindow($process.Id)) {
            $process.Refresh()
            if ($process.HasExited) { throw "Fairy exited before Presence became active" }
            if ([DateTime]::UtcNow -ge $activeDeadline) {
                throw "Timed out waiting to position the cursor over Fairy Presence"
            }
            Start-Sleep -Milliseconds 100
        }
    }

    if (-not $SkipRendererProbe) {
        $probeScript = Join-Path $root "desktop\scripts\probe-presence-webview.mjs"
        $probe = Start-Process node -ArgumentList @(
            $probeScript,
            "--port", $port,
            "--expected-mode", "liquid",
            "--experiment", $ExperimentMode,
            "--target-fps", $TargetFps,
            "--warmup-seconds", $WarmupSeconds,
            "--duration-seconds", $durationSeconds,
            "--output", $rendererOutput
        ) -PassThru -WindowStyle Hidden
    }

    $samples = [System.Collections.Generic.List[object]]::new()
    $warmupStartedAt = [DateTime]::UtcNow
    while (([DateTime]::UtcNow - $warmupStartedAt).TotalSeconds -lt $WarmupSeconds) {
        if ($KeepPresenceActive) {
            [FairyPresenceSoakCursor]::MoveToRenderWindow($process.Id) | Out-Null
        }
        Start-Sleep -Seconds 1
        $process.Refresh()
        if ($process.HasExited) { throw "Fairy exited during the soak warm-up" }
    }
    $startedAt = [DateTime]::UtcNow
    $process.Refresh()
    $initialTree = Get-ProcessTreeSnapshot $process.Id
    $lastCpuByProcess = $initialTree.cpu_by_process
    $lastPresenceCpuByProcess = $initialTree.presence_cpu_by_process
    $lastSampleAt = $startedAt
    while (([DateTime]::UtcNow - $startedAt).TotalSeconds -lt $durationSeconds) {
        if ($KeepPresenceActive) {
            [FairyPresenceSoakCursor]::MoveToRenderWindow($process.Id) | Out-Null
        }
        Start-Sleep -Seconds $SampleSeconds
        $process.Refresh()
        if ($process.HasExited) { throw "Fairy exited during the soak" }
        $now = [DateTime]::UtcNow
        $elapsed = [Math]::Max(0.001, ($now - $lastSampleAt).TotalSeconds)
        $tree = Get-ProcessTreeSnapshot $process.Id
        $cpuDelta = Get-ProcessTreeCpuDelta $lastCpuByProcess $tree.cpu_by_process
        $presenceCpuDelta = Get-ProcessTreeCpuDelta $lastPresenceCpuByProcess $tree.presence_cpu_by_process
        $samples.Add([PSCustomObject]@{
            elapsed_seconds = [Math]::Round(($now - $startedAt).TotalSeconds, 3)
            process_count = $tree.presence_process_count
            processes = $tree.presence_processes
            process_tree_count = $tree.process_count
            private_bytes = $tree.presence_private_bytes
            process_tree_private_bytes = $tree.private_bytes
            working_set_bytes = $tree.presence_working_set_bytes
            process_tree_working_set_bytes = $tree.working_set_bytes
            single_core_cpu_percent = [Math]::Round(($presenceCpuDelta / $elapsed) * 100, 3)
            process_tree_single_core_cpu_percent = [Math]::Round(($cpuDelta / $elapsed) * 100, 3)
        })
        $lastCpuByProcess = $tree.cpu_by_process
        $lastPresenceCpuByProcess = $tree.presence_cpu_by_process
        $lastSampleAt = $now
    }

    $renderer = $null
    $gpu = $null
    $rendererHeapGrowth = $null
    if (-not $SkipRendererProbe) {
        $probe.WaitForExit()
        if ($probe.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $rendererOutput)) {
            throw "WebView2 renderer soak probe failed"
        }
        $renderer = Get-Content -Raw -LiteralPath $rendererOutput | ConvertFrom-Json
        $gpu = $renderer.gpu
        $gpuRendererProperty = $renderer.gpu.PSObject.Properties["gl_renderer"]
        $gpuRenderer = if ($null -eq $gpuRendererProperty) { "" } else { [string]$gpuRendererProperty.Value }
        if (-not [string]::IsNullOrWhiteSpace($ExpectedGpuPattern) -and $gpuRenderer -notmatch $ExpectedGpuPattern) {
            throw "Presence renderer GPU '$gpuRenderer' did not match '$ExpectedGpuPattern'"
        }
        $rendererHeapGrowth = $renderer.live_heap_growth_bytes
    } elseif (-not [string]::IsNullOrWhiteSpace($ExpectedGpuPattern)) {
        throw "ExpectedGpuPattern requires the renderer probe"
    }
    $steadySamples = @($samples)
    $memoryGrowth = ($steadySamples[-1].private_bytes - $steadySamples[0].private_bytes)
    $processTreeMemoryGrowth = (
        $steadySamples[-1].process_tree_private_bytes -
        $steadySamples[0].process_tree_private_bytes
    )
    $averageCpu = ($steadySamples | Measure-Object single_core_cpu_percent -Average).Average
    $processTreeAverageCpu = (
        $steadySamples |
            Measure-Object process_tree_single_core_cpu_percent -Average
    ).Average
    $memoryBudget = $MaximumMemoryGrowthMb * 1MB
    $result = [PSCustomObject]@{
        warmup_seconds = $WarmupSeconds
        duration_seconds = $durationSeconds
        gpu_preference = $GpuPreference
        experiment_mode = $ExperimentMode
        optics_mode = $OpticsMode
        presence_kept_active = [bool]$KeepPresenceActive
        target_fps = $TargetFps
        renderer_probe_attached = -not $SkipRendererProbe
        samples = $samples
        average_single_core_cpu_percent = [Math]::Round($averageCpu, 3)
        private_memory_growth_bytes = $memoryGrowth
        process_tree_average_single_core_cpu_percent = [Math]::Round($processTreeAverageCpu, 3)
        process_tree_private_memory_growth_bytes = $processTreeMemoryGrowth
        gpu = $gpu
        renderer_initial = if ($null -eq $renderer) { $null } else { $renderer.initial }
        renderer = if ($null -eq $renderer) { $null } else { $renderer.final }
        renderer_pages_initial = if ($null -eq $renderer) { $null } else { $renderer.pages_initial }
        renderer_pages_final = if ($null -eq $renderer) { $null } else { $renderer.pages_final }
        renderer_live_heap_growth_bytes = $rendererHeapGrowth
        budgets = [PSCustomObject]@{
            maximum_single_core_cpu_percent = $MaximumSingleCoreCpuPercent
            maximum_memory_growth_bytes = $memoryBudget
            maximum_frame_p95_ms = 8
        }
    }
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8
    if ($memoryGrowth -gt $memoryBudget) {
        $actualMemoryGrowth = [Math]::Round($memoryGrowth / 1MB, 3)
        throw "Presence private memory growth $actualMemoryGrowth MiB exceeded $MaximumMemoryGrowthMb MiB"
    }
    if ($processTreeMemoryGrowth -gt $memoryBudget) {
        $actualTreeMemoryGrowth = [Math]::Round($processTreeMemoryGrowth / 1MB, 3)
        throw "Fairy process-tree private memory growth $actualTreeMemoryGrowth MiB exceeded $MaximumMemoryGrowthMb MiB"
    }
    if ($averageCpu -gt $MaximumSingleCoreCpuPercent) {
        throw "Presence CPU $([Math]::Round($averageCpu, 3)) exceeded $MaximumSingleCoreCpuPercent percent of one core"
    }
    if ($processTreeAverageCpu -gt $MaximumSingleCoreCpuPercent) {
        throw "Fairy process-tree CPU $([Math]::Round($processTreeAverageCpu, 3)) exceeded $MaximumSingleCoreCpuPercent percent of one core"
    }
    if (-not $SkipRendererProbe) {
        if ($null -ne $renderer.final.cpu_frame_p95_ms -and $renderer.final.cpu_frame_p95_ms -ge 8) {
            throw "Renderer CPU frame p95 exceeded 8ms"
        }
        if ($null -ne $renderer.final.gpu_frame_p95_ms -and $renderer.final.gpu_frame_p95_ms -ge 8) {
            throw "Renderer GPU frame p95 exceeded 8ms"
        }
        if ($null -eq $rendererHeapGrowth) {
            throw "Renderer live heap metrics were unavailable"
        }
        if ($rendererHeapGrowth -gt $memoryBudget) {
            $actualHeapGrowth = [Math]::Round($rendererHeapGrowth / 1MB, 3)
            throw "Renderer live heap growth $actualHeapGrowth MiB exceeded $MaximumMemoryGrowthMb MiB"
        }
    }
    $result | ConvertTo-Json -Depth 4
}
finally {
    if ($null -ne $probe -and -not $probe.HasExited) {
        Stop-ProcessTree $probe
    }
    if ($null -ne $process) {
        $process.Refresh()
        if (-not $process.HasExited) {
            Stop-ProcessTree $process
        }
        $process.Dispose()
    }
    Remove-VerifiedScratchDirectory $scratch $scratchPrefix
}
