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
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $destination
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardInput = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
        $startInfo.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        $previousConsoleInputEncoding = [Console]::InputEncoding
        try {
            [Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
            if (-not $process.Start()) {
                throw "Bundled Core health probe could not start"
            }
            $stdoutTask = $process.StandardOutput.ReadToEndAsync()
            $stderrTask = $process.StandardError.ReadToEndAsync()
            $process.StandardInput.WriteLine($request)
            $process.StandardInput.Close()
            if (-not $process.WaitForExit(30000)) {
                $process.Kill()
                throw "Bundled Core health probe timed out"
            }
            $responseText = $stdoutTask.GetAwaiter().GetResult()
            $null = $stderrTask.GetAwaiter().GetResult()
            if ($process.ExitCode -ne 0) {
                throw "Bundled Core health probe exited with code $($process.ExitCode)"
            }
        }
        finally {
            $process.Dispose()
            [Console]::InputEncoding = $previousConsoleInputEncoding
        }
        $responseLines = @(
            $responseText -split "\r?\n" |
                Where-Object { $_.Length -gt 0 }
        )
        if ($responseLines.Count -ne 1) {
            throw "Bundled Core health probe returned an invalid response count"
        }
        $response = $responseLines[0] | ConvertFrom-Json -ErrorAction Stop
        $resultProperty = $response.PSObject.Properties["result"]
        if ($null -eq $resultProperty) {
            $rpcCode = "missing"
            $errorCode = "missing"
            $errorProperty = $response.PSObject.Properties["error"]
            if ($null -ne $errorProperty) {
                $rpcCodeProperty = $errorProperty.Value.PSObject.Properties["code"]
                $dataProperty = $errorProperty.Value.PSObject.Properties["data"]
                if ($null -ne $rpcCodeProperty) {
                    $rpcCode = [string]$rpcCodeProperty.Value
                }
                if ($null -ne $dataProperty) {
                    $stableCodeProperty = $dataProperty.Value.PSObject.Properties["error_code"]
                    if ($null -ne $stableCodeProperty) {
                        $errorCode = [string]$stableCodeProperty.Value
                    }
                }
            }
            throw "Bundled Core health probe returned an error response (rpc=$rpcCode, code=$errorCode)"
        }
        $health = $resultProperty.Value
        $statusProperty = $health.PSObject.Properties["status"]
        $serviceProperty = $health.PSObject.Properties["service"]
        $protocolProperty = $health.PSObject.Properties["protocol"]
        if (
            $null -eq $statusProperty -or
            $null -eq $serviceProperty -or
            $null -eq $protocolProperty -or
            $statusProperty.Value -ne "ok" -or
            $serviceProperty.Value -ne "fairy-core" -or
            $protocolProperty.Value -ne "core-service-v1"
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
