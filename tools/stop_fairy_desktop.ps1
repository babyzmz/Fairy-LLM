[CmdletBinding()]
param(
    [string]$SessionName = "fairy_desktop_dev"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$stateFile = Join-Path $repoRoot "data\runtime\$SessionName.json"

# Ports we own across the dev stack.
$ManagedPorts = @{
    backend     = 8000
    vite        = 1420
    browser_cdp = 9778
    llama       = 12765
    cosyvoice   = 12970
}

function Stop-ProcessTree {
    param([int]$TargetPid)
    if ($TargetPid -le 0) {
        return
    }
    try {
        & taskkill.exe /PID $TargetPid /T /F | Out-Null
    }
    catch {
        try {
            Stop-Process -Id $TargetPid -Force -ErrorAction SilentlyContinue
        }
        catch {
        }
    }
}

function Stop-PortHolder {
    param([string]$Label, [int]$Port)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if ($conn) {
            Write-Host ("[Fairy Dev] Killing {0,-12} :{1} (pid {2})" -f $Label, $Port, $conn.OwningProcess)
            Stop-ProcessTree -TargetPid ([int]$conn.OwningProcess)
        }
    }
    catch {
    }
}

if (Test-Path $stateFile) {
    $state = Get-Content -Path $stateFile -Raw | ConvertFrom-Json
    Write-Host "[Fairy Dev] Stopping session $($state.session_name)"
    if ($state.tauri.pid)    { Stop-ProcessTree -TargetPid ([int]$state.tauri.pid) }
    if ($state.backend.pid)  { Stop-ProcessTree -TargetPid ([int]$state.backend.pid) }
    if ($state.browser.pid)  { Stop-ProcessTree -TargetPid ([int]$state.browser.pid) }
    if ($state.launcher_pid) { Stop-ProcessTree -TargetPid ([int]$state.launcher_pid) }
    Remove-Item -Path $stateFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "[Fairy Dev] No session file. Will still clean known ports."
}

# Clean any orphans on managed ports (CosyVoice / llama / Vite are children of backend
# and may survive abrupt kills).
foreach ($entry in $ManagedPorts.GetEnumerator()) {
    Stop-PortHolder -Label $entry.Key -Port $entry.Value
}

# Mop up known dev process names that linger after parent dies.
Get-Process -Name fairy-desktop, cargo -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Start-Sleep -Milliseconds 800

# Verify
$leftover = @()
foreach ($entry in $ManagedPorts.GetEnumerator()) {
    $conn = Get-NetTCPConnection -LocalPort $entry.Value -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        $leftover += "$($entry.Key):$($entry.Value) (pid $($conn.OwningProcess))"
    }
}

if ($leftover.Count -eq 0) {
    Write-Host "[Fairy Dev] All managed ports free." -ForegroundColor Green
} else {
    Write-Warning "[Fairy Dev] Still holding ports: $($leftover -join ', ')"
    exit 1
}
