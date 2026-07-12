[CmdletBinding()]
param(
    [string]$TargetTriple = "x86_64-pc-windows-msvc",
    [string]$DesktopProgram
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$coreProgram = Join-Path $root "desktop\src-tauri\binaries\fairy-core-${TargetTriple}.exe"
$gitProgram = Join-Path $root "desktop\src-tauri\runtime\git\cmd\git.exe"
if ([string]::IsNullOrWhiteSpace($DesktopProgram)) {
    $DesktopProgram = Join-Path $root "desktop\src-tauri\target\debug\fairy.exe"
}

foreach ($program in @($coreProgram, $gitProgram, $DesktopProgram)) {
    if (-not (Test-Path -LiteralPath $program -PathType Leaf)) {
        throw "Release composition dependency is missing: $program"
    }
}

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("fairy-v3-composition-" + [guid]::NewGuid().ToString("N"))
$dataDir = Join-Path $scratch "data"
$managedRoot = Join-Path $dataDir "workspaces"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $coreProgram
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardInput = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$startInfo.Environment["FAIRY_V3_DATA_DIR"] = $dataDir
$startInfo.Environment["FAIRY_LOCAL_WORKER_PROGRAM"] = $DesktopProgram
$startInfo.Environment["FAIRY_LOCAL_WORKER_ARGS_JSON"] = '["--local-worker"]'
$startInfo.Environment["FAIRY_GIT_PROGRAM"] = $gitProgram

$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $startInfo
try {
    if (-not $process.Start()) {
        throw "Bundled Core did not start"
    }

    $requests = @(
        '{"jsonrpc":"2.0","id":1,"method":"health","params":{}}',
        '{"jsonrpc":"2.0","id":2,"method":"projects.create","params":{"name":"Release composition","residency":"local_only"}}'
    )
    foreach ($request in $requests) {
        $process.StandardInput.WriteLine($request)
        $process.StandardInput.Flush()
    }
    $process.StandardInput.Close()

    if (-not $process.WaitForExit(30000)) {
        $process.Kill($true)
        throw "Bundled Core composition probe timed out"
    }
    $stderr = $process.StandardError.ReadToEnd()
    if ($process.ExitCode -ne 0) {
        throw "Bundled Core composition probe exited with code $($process.ExitCode): $stderr"
    }

    $responses = @($process.StandardOutput.ReadToEnd().Split([Environment]::NewLine, [System.StringSplitOptions]::RemoveEmptyEntries) | ForEach-Object { $_ | ConvertFrom-Json -ErrorAction Stop })
    if ($responses.Count -ne 2 -or $responses[0].result.protocol -ne "core-service-v1") {
        throw "Bundled Core did not return the expected health handshake"
    }
    $projectResponseHasError = $null -ne $responses[1].PSObject.Properties["error"]
    if ($projectResponseHasError -or $null -eq $responses[1].result.project.id) {
        throw "Bundled Core could not create a project through the Rust worker: $($responses[1] | ConvertTo-Json -Compress -Depth 10)"
    }
    if (-not (Test-Path -LiteralPath $managedRoot -PathType Container)) {
        throw "Rust worker did not create the managed project root"
    }
    $gitMetadata = @(Get-ChildItem -LiteralPath $managedRoot -Recurse -Force | Where-Object { $_.Name -eq ".git" })
    if ($gitMetadata.Count -eq 0) {
        throw "Bundled MinGit did not initialize managed Git metadata"
    }

    Write-Host "Bundled Core + Rust worker + MinGit composition passed for project $($responses[1].result.project.id)."
}
finally {
    if (-not $process.HasExited) {
        $process.Kill($true)
        $process.WaitForExit()
    }
    $process.Dispose()
    Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
}
