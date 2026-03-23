[CmdletBinding()]
param(
    [string]$SessionName = "fairy_desktop_dev"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$stateFile = Join-Path $repoRoot "data\runtime\$SessionName.json"

function Stop-ProcessTree {
    param([int]$Pid)
    if ($Pid -le 0) {
        return
    }
    try {
        & taskkill.exe /PID $Pid /T /F | Out-Null
    }
    catch {
        try {
            Stop-Process -Id $Pid -Force -ErrorAction SilentlyContinue
        }
        catch {
        }
    }
}

if (-not (Test-Path $stateFile)) {
    Write-Host "[Fairy Dev] No running session file found: $stateFile"
    exit 0
}

$state = Get-Content -Path $stateFile -Raw | ConvertFrom-Json

Write-Host "[Fairy Dev] Stopping session $($state.session_name)"
if ($state.tauri.pid) {
    Stop-ProcessTree -Pid ([int]$state.tauri.pid)
}
if ($state.backend.pid) {
    Stop-ProcessTree -Pid ([int]$state.backend.pid)
}
if ($state.launcher_pid) {
    Stop-ProcessTree -Pid ([int]$state.launcher_pid)
}

Remove-Item -Path $stateFile -Force -ErrorAction SilentlyContinue
Write-Host "[Fairy Dev] Stopped"
