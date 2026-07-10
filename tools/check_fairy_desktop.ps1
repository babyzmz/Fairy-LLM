[CmdletBinding()]
param()

# Read-only health snapshot of the Fairy Dev stack.
# Returns exit code 0 if everything looks healthy, 1 if anything is unexpected.

$ErrorActionPreference = "SilentlyContinue"

$ports = @(
    @{ Label = "Backend";   Port = 8000;  Probe = "http://127.0.0.1:8000/health";    Expect = "status=ok" },
    @{ Label = "Vite";      Port = 1420;  Probe = $null },
    @{ Label = "Browser";   Port = 9778;  Probe = "http://127.0.0.1:9778/json/version"; Expect = "browser-info" },
    @{ Label = "LLM";       Port = 12765; Probe = $null },
    @{ Label = "CosyVoice"; Port = 12970; Probe = "http://127.0.0.1:12970/health";   Expect = "ok=true" }
)

$any_unhealthy = $false

Write-Host ""
Write-Host "================ Fairy Dev status ================" -ForegroundColor Cyan

foreach ($entry in $ports) {
    $portConn = Get-NetTCPConnection -LocalPort $entry.Port -State Listen -ErrorAction SilentlyContinue
    if (-not $portConn) {
        Write-Host ("  [--] {0,-10} :{1,-6} not listening" -f $entry.Label, $entry.Port) -ForegroundColor DarkGray
        $any_unhealthy = $true
        continue
    }
    $pidOwner = $portConn.OwningProcess
    $proc = Get-Process -Id $pidOwner -ErrorAction SilentlyContinue
    $procName = if ($proc) { $proc.ProcessName } else { "<gone>" }
    $memMB = if ($proc) { [int]($proc.WorkingSet64 / 1MB) } else { 0 }

    $probeStatus = ""
    if ($entry.Probe) {
        try {
            $r = Invoke-RestMethod -Uri $entry.Probe -TimeoutSec 2
            if ($entry.Label -eq "Backend") {
                $probeStatus = "status=$($r.status)"
            } elseif ($entry.Label -eq "CosyVoice") {
                $probeStatus = "ok=$($r.ok) device=$($r.device)"
            } elseif ($entry.Label -eq "Browser") {
                $probeStatus = "$($r.Browser)"
            }
        } catch {
            $probeStatus = "probe failed: $_"
            $any_unhealthy = $true
        }
    }
    $line = ("  [OK] {0,-10} :{1,-6} pid={2,-6} mem={3,4}MB {4}" -f $entry.Label, $entry.Port, $pidOwner, $memMB, $procName)
    if ($probeStatus) { $line += "  ($probeStatus)" }
    Write-Host $line -ForegroundColor Green
}

Write-Host "==================================================" -ForegroundColor Cyan

# Auxiliary: companion runtime state if backend is up
try {
    $companion = Invoke-RestMethod -Uri "http://127.0.0.1:8000/companion/state" -TimeoutSec 2
    Write-Host ""
    Write-Host "Companion state:" -ForegroundColor Cyan
    Write-Host "  scene         : $($companion.scene)"
    Write-Host "  current_game  : $($companion.current_game)"
    Write-Host "  muted         : $($companion.muted)"
    Write-Host "  watcher game  : $($companion.watcher.active_game)"
    Write-Host "  fg window     : $($companion.watcher.last_window_title) / $($companion.watcher.last_process_name)"
} catch { }

# Session file presence
$stateFile = Join-Path (Split-Path -Parent $PSScriptRoot) "data\runtime\fairy_desktop_dev.json"
if (Test-Path $stateFile) {
    Write-Host ""
    Write-Host "Session file: $stateFile" -ForegroundColor Cyan
}

if ($any_unhealthy) {
    exit 1
} else {
    exit 0
}
