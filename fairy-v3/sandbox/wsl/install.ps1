[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RootfsPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-fA-F]{64}$")]
    [string]$RootfsSha256,

    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "Fairy\Sandbox\FairySandbox")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Distribution = "FairySandbox"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunnerPath = Join-Path (Split-Path -Parent $ScriptRoot) "runner\fairy_sandbox_runner.py"
$ConfigPath = Join-Path $ScriptRoot "etc\wsl.conf"
$Wsl = Get-Command wsl.exe -ErrorAction Stop

function Invoke-Wsl {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & $Wsl.Source @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "wsl.exe failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

function Write-WslFile {
    param(
        [Parameter(Mandatory = $true)][byte[]]$Content,
        [Parameter(Mandatory = $true)]
        [ValidateSet("runner", "config")]
        [string]$Target
    )

    $Destination = if ($Target -eq "runner") {
        "/usr/local/lib/fairy_sandbox_runner.py"
    }
    else {
        "/etc/wsl.conf"
    }
    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $Wsl.Source
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardInput = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.CreateNoWindow = $true
    $StartInfo.Arguments = (
        "--distribution FairySandbox --user root --exec /usr/bin/tee " + $Destination
    )
    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    if (-not $Process.Start()) {
        throw "failed to start wsl.exe for $Destination"
    }
    try {
        $Process.StandardInput.BaseStream.Write($Content, 0, $Content.Length)
        $Process.StandardInput.Close()
        $null = $Process.StandardOutput.ReadToEnd()
        $StandardError = $Process.StandardError.ReadToEnd()
        $Process.WaitForExit()
        if ($Process.ExitCode -ne 0) {
            throw "failed to write $Destination inside FairySandbox: $StandardError"
        }
    }
    finally {
        $Process.Dispose()
    }
}

$ResolvedRootfs = (Resolve-Path -LiteralPath $RootfsPath -ErrorAction Stop).Path
$ActualHash = (Get-FileHash -LiteralPath $ResolvedRootfs -Algorithm SHA256).Hash
if ($ActualHash -ine $RootfsSha256) {
    throw "Rootfs SHA-256 does not match RootfsSha256"
}
if (-not (Test-Path -LiteralPath $RunnerPath -PathType Leaf)) {
    throw "Sandbox runner is missing: $RunnerPath"
}
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Sandbox wsl.conf is missing: $ConfigPath"
}

$Installed = @(& $Wsl.Source --list --quiet) | ForEach-Object { $_.Trim() }
if ($Installed -contains $Distribution) {
    throw "FairySandbox is already installed; this installer never overwrites a distribution"
}

$ResolvedInstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$null = New-Item -ItemType Directory -Path $ResolvedInstallRoot -Force
Invoke-Wsl @(
    "--import",
    "FairySandbox",
    $ResolvedInstallRoot,
    $ResolvedRootfs,
    "--version",
    "2"
)

Invoke-Wsl @(
    "--distribution", "FairySandbox", "--user", "root", "--exec",
    "/usr/bin/apt-get", "update"
)
Invoke-Wsl @(
    "--distribution", "FairySandbox", "--user", "root", "--exec",
    "/usr/bin/apt-get", "install", "--yes", "--no-install-recommends",
    "bubblewrap", "ca-certificates", "python3"
)

& $Wsl.Source --distribution FairySandbox --user root --exec /usr/bin/id --user fairy *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-Wsl @(
        "--distribution", "FairySandbox", "--user", "root", "--exec",
        "/usr/sbin/useradd", "--create-home", "--shell", "/usr/sbin/nologin", "fairy"
    )
}
Invoke-Wsl @(
    "--distribution", "FairySandbox", "--user", "root", "--exec",
    "/usr/bin/mkdir", "--parents", "/usr/local/lib", "/usr/local/bin", "/var/lib/fairy-sandbox/jobs",
    "/var/lib/fairy-sandbox/workspaces"
)

Write-WslFile -Content ([System.IO.File]::ReadAllBytes($RunnerPath)) -Target "runner"
Write-WslFile -Content ([System.IO.File]::ReadAllBytes($ConfigPath)) -Target "config"
Invoke-Wsl @(
    "--distribution", "FairySandbox", "--user", "root", "--exec",
    "/usr/bin/chmod", "0755", "/usr/local/lib/fairy_sandbox_runner.py"
)
Invoke-Wsl @(
    "--distribution", "FairySandbox", "--user", "root", "--exec",
    "/usr/bin/chmod", "0644", "/etc/wsl.conf"
)
Invoke-Wsl @(
    "--distribution", "FairySandbox", "--user", "root", "--exec",
    "/usr/bin/ln", "--symbolic", "--force", "/usr/local/lib/fairy_sandbox_runner.py",
    "/usr/local/bin/fairy-sandbox-runner"
)
Invoke-Wsl @(
    "--distribution", "FairySandbox", "--user", "root", "--exec",
    "/usr/bin/ln", "--symbolic", "--force", "/usr/local/lib/fairy_sandbox_runner.py",
    "/usr/local/bin/fairy-sandbox-health"
)
Invoke-Wsl @(
    "--distribution", "FairySandbox", "--user", "root", "--exec",
    "/usr/bin/chown", "--recursive", "fairy:fairy", "/var/lib/fairy-sandbox"
)

Invoke-Wsl @("--terminate", "FairySandbox")
$Health = & $Wsl.Source --distribution FairySandbox --user fairy --exec (
    "/usr/local/bin/fairy-sandbox-health"
) --json
if ($LASTEXITCODE -ne 0) {
    throw "FairySandbox health verification failed"
}
$HealthDocument = $Health | ConvertFrom-Json
if (
    $HealthDocument.executor -ne "wsl_fairy_sandbox" -or
    $HealthDocument.runner_version -ne "1.0.0" -or
    $HealthDocument.user -ne "fairy" -or
    $HealthDocument.uid -le 0
) {
    throw "FairySandbox returned an invalid health document"
}

Write-Host "FairySandbox WSL 2 installation and health verification completed."
