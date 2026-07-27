[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Executable,
    [Parameter(Mandatory)]
    [string]$DataDirectory,
    [switch]$ConfirmDataDirectory,
    [ValidateRange(4.0, 168.0)]
    [double]$DurationHours = 4.0,
    [ValidateRange(5, 60)]
    [int]$SampleSeconds = 10,
    [ValidateRange(1, 120)]
    [int]$StartupTimeoutMinutes = 15,
    [ValidateRange(1, 60)]
    [int]$StopTimeoutMinutes = 15,
    [string]$OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "The Realtime Companion wall-clock soak requires Windows."
}
if (-not $ConfirmDataDirectory) {
    throw "Pass -ConfirmDataDirectory after selecting a dedicated Fairy soak data directory."
}
$resolvedExecutable = [System.IO.Path]::GetFullPath($Executable)
$resolvedDataDirectory = [System.IO.Path]::GetFullPath($DataDirectory)
if (-not [System.IO.Path]::IsPathFullyQualified($Executable) -or
    -not (Test-Path -LiteralPath $resolvedExecutable -PathType Leaf)) {
    throw "A fully qualified Fairy executable is required."
}
if (-not [System.IO.Path]::IsPathFullyQualified($DataDirectory) -or
    -not (Test-Path -LiteralPath $resolvedDataDirectory -PathType Container)) {
    throw "A fully qualified existing Fairy data directory is required."
}
$modelStatePath = Join-Path $resolvedDataDirectory "models\minicpm-o-4.5\install-state.json"
if (-not (Test-Path -LiteralPath $modelStatePath -PathType Leaf)) {
    throw "The dedicated data directory has no managed MiniCPM install state."
}
$modelState = Get-Content -Raw -LiteralPath $modelStatePath | ConvertFrom-Json
if ([int]$modelState.schema_version -ne 1 -or [string]$modelState.phase -ne "ready") {
    throw "The managed MiniCPM model must be in the ready phase before the soak."
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path (
        [System.IO.Path]::GetTempPath()
    ) "fairy-realtime-companion-soak.json"
}
$resolvedOutputPath = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $resolvedOutputPath
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
if (Test-Path -LiteralPath $resolvedOutputPath) {
    throw "Refusing to overwrite existing soak evidence: $resolvedOutputPath"
}
if (@(Get-Process -Name "fairy" -ErrorAction SilentlyContinue).Count -gt 0) {
    throw "Close all existing Fairy processes before starting the controlled soak."
}

function Get-AvailablePort {
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        0
    )
    $listener.Start()
    try {
        return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
}

function Stop-ProcessTree([System.Diagnostics.Process]$Target) {
    if ($null -eq $Target) { return }
    $rows = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId)
    $treeIds = [System.Collections.Generic.HashSet[int]]::new()
    [void]$treeIds.Add($Target.Id)
    do {
        $added = $false
        foreach ($row in $rows) {
            if ($treeIds.Contains([int]$row.ParentProcessId) -and
                $treeIds.Add([int]$row.ProcessId)) {
                $added = $true
            }
        }
    } while ($added)
    $Target.Refresh()
    if (-not $Target.HasExited) {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "SilentlyContinue"
            & taskkill.exe /PID $Target.Id /T /F 2>&1 | Out-Null
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
        $Target.Refresh()
        if (-not $Target.HasExited -and -not $Target.WaitForExit(5000)) {
            $Target.Kill()
            $Target.WaitForExit()
        }
    }
    $cleanupDeadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $remaining = @(
            $treeIds |
                Where-Object { $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue) }
        )
        if ($remaining.Count -eq 0) { return }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $cleanupDeadline)
    if ($remaining.Count -gt 0) {
        throw "Controlled Fairy process cleanup did not complete."
    }
}

$root = Split-Path -Parent $PSScriptRoot
$probeScript = Join-Path $root "desktop\scripts\probe-realtime-companion-soak.mjs"
if (-not (Test-Path -LiteralPath $probeScript -PathType Leaf)) {
    throw "Realtime soak WebView probe is missing."
}
$port = Get-AvailablePort
$durationSeconds = [int][Math]::Ceiling($DurationHours * 60 * 60)
$process = $null
$probeSucceeded = $false
$cleanupConfirmed = $false
try {
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $resolvedExecutable
    $startInfo.WorkingDirectory = Split-Path -Parent $resolvedExecutable
    $startInfo.UseShellExecute = $false
    $startInfo.Environment["FAIRY_DESKTOP_DATA_DIR"] = $resolvedDataDirectory
    $startInfo.Environment["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        "--remote-debugging-port=$port"
    )
    $process = [System.Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) {
        throw "Fairy did not start."
    }
    Write-Host (
        "Fairy started with the dedicated soak profile. In the main window, " +
        "Verify the Local MiniCPM runtime, then start Realtime Companion using " +
        "the local backend. The gate will reject Cloud fallback."
    )
    & node $probeScript `
        --port $port `
        --duration-seconds $durationSeconds `
        --sample-seconds $SampleSeconds `
        --startup-timeout-seconds ($StartupTimeoutMinutes * 60) `
        --stop-timeout-seconds ($StopTimeoutMinutes * 60) `
        --output $resolvedOutputPath
    if ($LASTEXITCODE -ne 0) {
        throw "Realtime Companion soak probe failed."
    }
    $probeSucceeded = $true
}
finally {
    Stop-ProcessTree $process
    $cleanupConfirmed = $true
}

if (-not $probeSucceeded -or -not (Test-Path -LiteralPath $resolvedOutputPath -PathType Leaf)) {
    throw "Realtime Companion soak produced no passing evidence."
}
$report = Get-Content -Raw -LiteralPath $resolvedOutputPath | ConvertFrom-Json
if ([string]$report.status -ne "passed" -or
    [int]$report.observed_duration_seconds -lt 14400 -or
    [string]$report.backend -ne "local_mini_cpm_o45" -or
    [string]$report.terminal_session_status -ne "completed" -or
    [int]$report.digest_count -lt 1) {
    throw "Realtime Companion soak evidence failed final validation."
}
$report | Add-Member `
    -NotePropertyName "process_cleanup_confirmed" `
    -NotePropertyValue $cleanupConfirmed
$temporaryOutput = "$resolvedOutputPath.tmp-$PID"
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temporaryOutput -Encoding utf8
Move-Item -LiteralPath $temporaryOutput -Destination $resolvedOutputPath -Force
Write-Host "Realtime Companion soak passed: $resolvedOutputPath"
