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
$RuntimeRunnerPath = Join-Path (
    (Split-Path -Parent $ScriptRoot)
) "runner\fairy_runtime_supervisor.py"
$ConfigPath = Join-Path $ScriptRoot "etc\wsl.conf"
$ToolchainPath = Join-Path $ScriptRoot "install-toolchain.sh"
$Wsl = Get-Command wsl.exe -ErrorAction Stop

function Invoke-Wsl {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & $Wsl.Source @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "wsl.exe failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

function Test-WslUser {
    param([Parameter(Mandatory = $true)][string]$User)

    # Windows PowerShell promotes native stderr to an error record when the
    # script-wide preference is Stop. A missing user is an expected probe result.
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $Wsl.Source --distribution FairySandbox --user root --exec (
            "/usr/bin/id"
        ) --user $User *> $null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
}

function Write-WslFile {
    param(
        [Parameter(Mandatory = $true)][byte[]]$Content,
        [Parameter(Mandatory = $true)]
        [ValidateSet("runner", "runtime-runner", "config", "toolchain")]
        [string]$Target
    )

    $Destination = if ($Target -eq "runner") {
        "/usr/local/lib/fairy_sandbox_runner.py"
    }
    elseif ($Target -eq "runtime-runner") {
        "/usr/local/lib/fairy_runtime_supervisor.py"
    }
    elseif ($Target -eq "toolchain") {
        "/usr/local/lib/fairy_install_toolchain.sh"
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
if (-not (Test-Path -LiteralPath $RuntimeRunnerPath -PathType Leaf)) {
    throw "Runtime supervisor is missing: $RuntimeRunnerPath"
}
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Sandbox wsl.conf is missing: $ConfigPath"
}
if (-not (Test-Path -LiteralPath $ToolchainPath -PathType Leaf)) {
    throw "Sandbox toolchain installer is missing: $ToolchainPath"
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
    "bubblewrap", "ca-certificates", "cargo", "curl", "gnupg", "python3", "python3-pip",
    "ripgrep",
    "python3-venv", "xz-utils"
)

if (-not (Test-WslUser -User "fairy")) {
    Invoke-Wsl @(
        "--distribution", "FairySandbox", "--user", "root", "--exec",
        "/usr/sbin/useradd", "--create-home", "--shell", "/usr/sbin/nologin", "fairy"
    )
}
Invoke-Wsl @(
    "--distribution", "FairySandbox", "--user", "root", "--exec",
    "/usr/bin/mkdir", "--parents", "/usr/local/lib", "/usr/local/bin", "/var/lib/fairy-sandbox/jobs",
    "/var/lib/fairy-sandbox/workspaces",
    "/var/lib/fairy-sandbox/dependencies", "/var/lib/fairy-sandbox/runtimes"
)

Write-WslFile -Content ([System.IO.File]::ReadAllBytes($RunnerPath)) -Target "runner"
Write-WslFile `
    -Content ([System.IO.File]::ReadAllBytes($RuntimeRunnerPath)) `
    -Target "runtime-runner"
Write-WslFile -Content ([System.IO.File]::ReadAllBytes($ConfigPath)) -Target "config"
Write-WslFile `
    -Content ([System.IO.File]::ReadAllBytes($ToolchainPath)) `
    -Target "toolchain"
Invoke-Wsl @(
    "--distribution", "FairySandbox", "--user", "root", "--exec",
    "/usr/bin/chmod", "0755", "/usr/local/lib/fairy_sandbox_runner.py"
)
Invoke-Wsl @(
    "--distribution", "FairySandbox", "--user", "root", "--exec",
    "/usr/bin/chmod", "0755", "/usr/local/lib/fairy_runtime_supervisor.py"
)
Invoke-Wsl @(
    "--distribution", "FairySandbox", "--user", "root", "--exec",
    "/usr/bin/chmod", "0644", "/etc/wsl.conf"
)
Invoke-Wsl @(
    "--distribution", "FairySandbox", "--user", "root", "--exec",
    "/usr/bin/chmod", "0755", "/usr/local/lib/fairy_install_toolchain.sh"
)
Invoke-Wsl @(
    "--distribution", "FairySandbox", "--user", "root", "--exec",
    "/usr/local/lib/fairy_install_toolchain.sh"
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
    "/usr/bin/ln", "--symbolic", "--force", "/usr/local/lib/fairy_runtime_supervisor.py",
    "/usr/local/bin/fairy-runtime-supervisor"
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
    $HealthDocument.runner_version -ne "1.1.0" -or
    $HealthDocument.user -ne "fairy" -or
    $HealthDocument.uid -le 0 -or
    $HealthDocument.toolchain.node -ne "v24.18.0" -or
    $HealthDocument.toolchain.pnpm -ne "10.34.4" -or
    $HealthDocument.toolchain.yarn -ne "1.22.22" -or
    $HealthDocument.toolchain.uv -notmatch "^uv 0\.11\.28(?: |$)" -or
    $HealthDocument.toolchain.rg -notmatch "^ripgrep 1[4-9]\."
) {
    throw "FairySandbox returned an invalid health document"
}
$RuntimeHealth = & $Wsl.Source --distribution FairySandbox --user fairy --exec (
    "/usr/local/bin/fairy-runtime-supervisor"
) health
if ($LASTEXITCODE -ne 0) {
    throw "FairySandbox Runtime supervisor health verification failed"
}
$RuntimeHealthDocument = $RuntimeHealth | ConvertFrom-Json
if (
    $RuntimeHealthDocument.executor -ne "wsl_fairy_runtime" -or
    $RuntimeHealthDocument.runner_version -ne "1.0.0" -or
    $RuntimeHealthDocument.user -ne "fairy" -or
    $RuntimeHealthDocument.uid -le 0 -or
    $RuntimeHealthDocument.toolchain.node -ne "v24.18.0" -or
    $RuntimeHealthDocument.toolchain.pnpm -ne "10.34.4" -or
    $RuntimeHealthDocument.toolchain.yarn -ne "1.22.22" -or
    $RuntimeHealthDocument.toolchain.uv -notmatch "^uv 0\.11\.28(?: |$)" -or
    $RuntimeHealthDocument.config.'automount.enabled' -ne $false -or
    $RuntimeHealthDocument.config.'automount.mountFsTab' -ne $false -or
    $RuntimeHealthDocument.config.'interop.enabled' -ne $false -or
    $RuntimeHealthDocument.config.'interop.appendWindowsPath' -ne $false
) {
    throw "FairySandbox returned an invalid Runtime supervisor health document"
}

Write-Host "FairySandbox WSL 2 installation and health verification completed."
