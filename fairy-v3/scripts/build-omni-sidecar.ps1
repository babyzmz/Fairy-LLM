[CmdletBinding()]
param(
    [ValidateSet("contract", "production")]
    [string]$Profile = "contract",
    [string]$ModelRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runtimeRoot = Join-Path $projectRoot "desktop\native\omni-runtime"
$buildScript = Join-Path $runtimeRoot "scripts\build.ps1"
$manifestPath = Join-Path $projectRoot "desktop\src-tauri\resources\omni\minicpm-o-4.5.json"
$stageRoot = Join-Path $projectRoot "desktop\src-tauri\runtime\omni"
$stageExecutable = Join-Path $stageRoot "fairy-omni-runtime.exe"
$stageProfile = Join-Path $stageRoot "build-profile.txt"
$nativeProfile = if ($Profile -eq "production") { "production-cuda" } else { "contract" }

if ($Profile -eq "production") {
    if ([string]::IsNullOrWhiteSpace($ModelRoot)) {
        throw "Production Omni staging requires -ModelRoot with the verified three-file model."
    }
    $ModelRoot = [IO.Path]::GetFullPath($ModelRoot)
    if (-not (Test-Path -LiteralPath $ModelRoot -PathType Container)) {
        throw "The production Omni model root does not exist."
    }
}
else {
    $ModelRoot = $runtimeRoot
}

$buildOutput = @(
    & powershell -NoProfile -ExecutionPolicy Bypass -File $buildScript `
        -Profile $nativeProfile `
        -Test
)
if ($LASTEXITCODE -ne 0) {
    throw "The Omni '$nativeProfile' build failed."
}
$executable = [IO.Path]::GetFullPath([string]$buildOutput[-1])
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "The Omni build did not produce the declared executable."
}

$reportText = @(
    & $executable `
        --self-test `
        --manifest $manifestPath `
        --model-root $ModelRoot
)
if ($LASTEXITCODE -ne 0 -or $reportText.Count -ne 1) {
    throw "The Omni '$nativeProfile' self-test did not complete cleanly."
}
$report = $reportText[0] | ConvertFrom-Json
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$lock = Get-Content -LiteralPath (Join-Path $runtimeRoot "upstream.lock.json") -Raw -Encoding UTF8 |
    ConvertFrom-Json

if ([int]$report.schema_version -ne 2 -or
    [string]$report.build_profile -ne $nativeProfile -or
    [string]$report.runtime_compatibility -ne [string]$manifest.runtime_compatibility -or
    [string]$report.manifest_digest -ne [string]$manifest.manifest_digest -or
    [string]$report.model_version -ne [string]$manifest.version -or
    [string]$report.upstream_runtime_revision -ne [string]$lock.upstream.revision -or
    [string]$report.patch_set_digest -ne [string]$lock.patch_set.digest) {
    throw "The Omni self-test identity does not match the pinned build inputs."
}

if ($Profile -eq "production") {
    if (-not [bool]$report.cuda_compiled -or
        -not [bool]$report.backend_ready -or
        [string]$report.model_probe -ne "ready") {
        throw "The production Omni runtime did not pass CUDA and model probing."
    }
}
elseif ([bool]$report.cuda_compiled -or [bool]$report.backend_ready) {
    throw "The contract runtime unexpectedly claimed production capability."
}

New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
$temporaryExecutable = Join-Path $stageRoot "fairy-omni-runtime.exe.$PID.tmp"
$temporaryProfile = Join-Path $stageRoot "build-profile.txt.$PID.tmp"
try {
    Copy-Item -LiteralPath $executable -Destination $temporaryExecutable -Force
    [IO.File]::WriteAllText(
        $temporaryProfile,
        "$nativeProfile`n",
        [Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporaryExecutable -Destination $stageExecutable -Force
    Move-Item -LiteralPath $temporaryProfile -Destination $stageProfile -Force
}
finally {
    Remove-Item -LiteralPath $temporaryExecutable -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $temporaryProfile -Force -ErrorAction SilentlyContinue
}

[pscustomobject]@{
    schema_version = 1
    profile = $nativeProfile
    executable = $stageExecutable
    backend_ready = [bool]$report.backend_ready
} | ConvertTo-Json -Compress
