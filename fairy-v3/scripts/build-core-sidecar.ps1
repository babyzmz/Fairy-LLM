[CmdletBinding()]
param(
    [string]$TargetTriple = "x86_64-pc-windows-msvc"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$uvCandidate = "C:\Python313\Scripts\uv.exe"
$uv = if (Test-Path -LiteralPath $uvCandidate) { $uvCandidate } else { "uv" }
$outputRoot = Join-Path $root "desktop\src-tauri\binaries"
$destination = Join-Path $outputRoot "fairy-core-${TargetTriple}.exe"
$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "fairy-v3-core-sidecar"
$dist = Join-Path $scratch "dist"
$work = Join-Path $scratch "work"
$spec = Join-Path $scratch "spec"

Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $dist, $work, $spec, $outputRoot | Out-Null

try {
    Push-Location -LiteralPath $root
    try {
        & $uv run --project capabilities --group build pyinstaller `
            --noconfirm `
            --clean `
            --noupx `
            --onefile `
            --name fairy-core `
            --distpath $dist `
            --workpath $work `
            --specpath $spec `
            --paths (Join-Path $root "core\src") `
            --paths (Join-Path $root "capabilities\src") `
            --collect-all fairy_core `
            --collect-all fairy_capabilities `
            --add-data "$(Join-Path $root 'resources');resources" `
            (Join-Path $root "capabilities\src\fairy_capabilities\stdio.py")
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }

    $built = Join-Path $dist "fairy-core.exe"
    if (-not (Test-Path -LiteralPath $built -PathType Leaf)) {
        throw "PyInstaller did not produce fairy-core.exe"
    }
    Copy-Item -LiteralPath $built -Destination $destination -Force

    $probeData = Join-Path $scratch "probe-data"
    New-Item -ItemType Directory -Force -Path $probeData | Out-Null
    $previousDataDir = $env:FAIRY_V3_DATA_DIR
    try {
        $env:FAIRY_V3_DATA_DIR = $probeData
        $request = '{"jsonrpc":"2.0","id":1,"method":"health","params":{}}'
        $responseLine = $request | & $destination
        if ($LASTEXITCODE -ne 0) {
            throw "Bundled Core health probe exited with code $LASTEXITCODE"
        }
        $response = $responseLine | ConvertFrom-Json -ErrorAction Stop
        if (
            $response.result.status -ne "ok" -or
            $response.result.service -ne "fairy-core" -or
            $response.result.protocol -ne "core-service-v1"
        ) {
            throw "Bundled Core returned an invalid health handshake"
        }
    }
    finally {
        $env:FAIRY_V3_DATA_DIR = $previousDataDir
    }

    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $destination).Hash.ToLowerInvariant()
    Write-Host "Built $destination"
    Write-Host "SHA256 $hash"
}
finally {
    Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
}
