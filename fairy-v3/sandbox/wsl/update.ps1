[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Distribution = "FairySandbox"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunnerPath = Join-Path (Split-Path -Parent $ScriptRoot) "runner\fairy_sandbox_runner.py"
$StagingPath = "/usr/local/lib/fairy_sandbox_runner.py.update"
$RunnerTarget = "/usr/local/lib/fairy_sandbox_runner.py"
$BackupPath = "/usr/local/lib/fairy_sandbox_runner.py.previous"
$Wsl = Get-Command wsl.exe -ErrorAction Stop

function Invoke-Wsl {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & $Wsl.Source @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "wsl.exe failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

function Write-StagedRunner {
    param([Parameter(Mandatory = $true)][byte[]]$Content)

    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $Wsl.Source
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardInput = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.CreateNoWindow = $true
    $StartInfo.Arguments = (
        "--distribution $Distribution --user root --exec /usr/bin/tee $StagingPath"
    )
    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    if (-not $Process.Start()) {
        throw "failed to stage the FairySandbox runner"
    }
    try {
        $Process.StandardInput.BaseStream.Write($Content, 0, $Content.Length)
        $Process.StandardInput.Close()
        $null = $Process.StandardOutput.ReadToEnd()
        $StandardError = $Process.StandardError.ReadToEnd()
        $Process.WaitForExit()
        if ($Process.ExitCode -ne 0) {
            throw "failed to stage the FairySandbox runner: $StandardError"
        }
    }
    finally {
        $Process.Dispose()
    }
}

if (-not (Test-Path -LiteralPath $RunnerPath -PathType Leaf)) {
    throw "Sandbox runner source is missing: $RunnerPath"
}

$ExistingHealth = & $Wsl.Source --distribution $Distribution --user fairy --exec (
    "/usr/local/bin/fairy-sandbox-health"
) --json | ConvertFrom-Json
if (
    $LASTEXITCODE -ne 0 -or
    $ExistingHealth.executor -ne "wsl_fairy_sandbox" -or
    $ExistingHealth.user -ne "fairy" -or
    $ExistingHealth.uid -le 0
) {
    throw "The installed FairySandbox identity could not be attested"
}

Invoke-Wsl @(
    "--distribution", $Distribution, "--user", "root", "--exec",
    "/usr/bin/apt-get", "update"
)
Invoke-Wsl @(
    "--distribution", $Distribution, "--user", "root", "--exec",
    "/usr/bin/apt-get", "install", "--yes", "--no-install-recommends", "ripgrep"
)
Write-StagedRunner -Content ([System.IO.File]::ReadAllBytes($RunnerPath))

try {
    Invoke-Wsl @(
        "--distribution", $Distribution, "--user", "root", "--exec",
        "/usr/bin/python3", "-m", "py_compile", $StagingPath
    )
    Invoke-Wsl @(
        "--distribution", $Distribution, "--user", "root", "--exec",
        "/usr/bin/chmod", "0755", $StagingPath
    )
    Invoke-Wsl @(
        "--distribution", $Distribution, "--user", "root", "--exec",
        "/usr/bin/cp", "--preserve=mode,ownership,timestamps", $RunnerTarget, $BackupPath
    )
    Invoke-Wsl @(
        "--distribution", $Distribution, "--user", "root", "--exec",
        "/usr/bin/mv", $StagingPath, $RunnerTarget
    )
    Invoke-Wsl @(
        "--distribution", $Distribution, "--user", "root", "--exec",
        "/usr/bin/ln", "--symbolic", "--force", $RunnerTarget,
        "/usr/local/bin/fairy-sandbox-runner"
    )
    Invoke-Wsl @(
        "--distribution", $Distribution, "--user", "root", "--exec",
        "/usr/bin/ln", "--symbolic", "--force", $RunnerTarget,
        "/usr/local/bin/fairy-sandbox-health"
    )
    Invoke-Wsl @("--terminate", $Distribution)
    $Health = & $Wsl.Source --distribution $Distribution --user fairy --exec (
        "/usr/local/bin/fairy-sandbox-health"
    ) --json | ConvertFrom-Json
    if (
        $LASTEXITCODE -ne 0 -or
        $Health.executor -ne "wsl_fairy_sandbox" -or
        $Health.runner_version -ne "1.1.0" -or
        $Health.toolchain.rg -notmatch "^ripgrep 1[4-9]\."
    ) {
        throw "Updated FairySandbox health verification failed"
    }
}
catch {
    & $Wsl.Source --distribution $Distribution --user root --exec (
        "/usr/bin/test"
    ) -f $BackupPath
    if ($LASTEXITCODE -eq 0) {
        Invoke-Wsl @(
            "--distribution", $Distribution, "--user", "root", "--exec",
            "/usr/bin/cp", "--preserve=mode,ownership,timestamps", $BackupPath, $RunnerTarget
        )
        Invoke-Wsl @("--terminate", $Distribution)
    }
    throw
}

Write-Host "FairySandbox runner updated and attested: 1.1.0"
