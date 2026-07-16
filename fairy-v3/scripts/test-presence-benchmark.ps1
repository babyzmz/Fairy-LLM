[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Executable,
    [ValidateRange(10, 600)]
    [int]$DurationSeconds = 60,
    [ValidateRange(0, 60)]
    [int]$WarmupSeconds = 5,
    [ValidateSet(60, 144)]
    [int[]]$TargetFps = @(60, 144),
    [string]$OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$soak = Join-Path $PSScriptRoot "test-presence-soak.ps1"
$modes = @(
    "normal",
    "static-backdrop",
    "capture-only",
    "ipc-upload-only",
    "single-renderer",
    "no-particles",
    "no-refraction"
)
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path ([System.IO.Path]::GetTempPath()) "fairy-presence-benchmark.json"
}
$outputDirectory = Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath))
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

$results = [System.Collections.Generic.List[object]]::new()
foreach ($fps in $TargetFps) {
    foreach ($mode in $modes) {
        $samplePath = Join-Path $outputDirectory "presence-$mode-$fps.json"
        $errorMessage = $null
        try {
            & $soak `
                -Executable $Executable `
                -DurationMinutes ($DurationSeconds / 60.0) `
                -SampleSeconds 1 `
                -WarmupSeconds $WarmupSeconds `
                -MaximumMemoryGrowthMb 100 `
                -MaximumSingleCoreCpuPercent 100 `
                -ExperimentMode $mode `
                -OpticsMode enhanced `
                -KeepPresenceActive `
                -TargetFps $fps `
                -OutputPath $samplePath | Out-Null
        }
        catch {
            $errorMessage = $_.Exception.Message
        }
        if (-not (Test-Path -LiteralPath $samplePath -PathType Leaf)) {
            $results.Add([PSCustomObject]@{
                mode = $mode
                target_fps = $fps
                error = if ($null -eq $errorMessage) { "benchmark output is missing" } else { $errorMessage }
            })
            continue
        }
        $sample = Get-Content -Raw -LiteralPath $samplePath | ConvertFrom-Json
        $lastProcessSample = @($sample.samples)[-1]
        $results.Add([PSCustomObject]@{
            mode = $mode
            target_fps = $fps
            fps_avg = $sample.renderer.fps_avg
            fps_p1 = $sample.renderer.fps_p1
            cpu_single_core_percent = $sample.average_single_core_cpu_percent
            gpu_frame_p95_ms = $sample.renderer.gpu_frame_p95_ms
            backdrop_fps_avg = $sample.renderer.backdrop_fps_avg
            capture_p95_ms = $sample.renderer.capture_p95_ms
            ipc_p95_ms = $sample.renderer.ipc_p95_ms
            upload_cpu_p95_ms = $sample.renderer.upload_cpu_p95_ms
            deadline_miss_count = $sample.renderer.deadline_miss_count
            dropped_frame_count = $sample.renderer.dropped_frame_count
            working_set_bytes = $lastProcessSample.working_set_bytes
            error = $errorMessage
        })
    }
}

$result = [PSCustomObject]@{
    generated_at = [DateTime]::UtcNow.ToString("o")
    executable = (Resolve-Path -LiteralPath $Executable).Path
    duration_seconds = $DurationSeconds
    warmup_seconds = $WarmupSeconds
    windows = (Get-CimInstance Win32_OperatingSystem | Select-Object Caption, BuildNumber)
    cpu = (Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name)
    gpu = @(Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, CurrentRefreshRate)
    results = $results
}
$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8
$result | ConvertTo-Json -Depth 8
