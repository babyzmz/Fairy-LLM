[CmdletBinding()]
param(
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-ContainedPath {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string]$CandidatePath
    )

    $base = [IO.Path]::GetFullPath($BasePath).TrimEnd('\', '/')
    $candidate = [IO.Path]::GetFullPath($CandidatePath).TrimEnd('\', '/')
    if (-not $candidate.StartsWith("$base$([IO.Path]::DirectorySeparatorChar)", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Tool path '$candidate' escapes '$base'."
    }
    return $candidate
}

$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$lock = Get-Content -LiteralPath (Join-Path $runtimeRoot "upstream.lock.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$cmake = $lock.toolchain.cmake
$toolsRoot = [IO.Path]::GetFullPath((Join-Path $runtimeRoot ".tools"))
$downloadRoot = Assert-ContainedPath -BasePath $toolsRoot -CandidatePath (Join-Path $toolsRoot "downloads")
$archivePath = Assert-ContainedPath -BasePath $toolsRoot -CandidatePath (Join-Path $downloadRoot ([string]$cmake.archive))
$installRoot = Assert-ContainedPath -BasePath $toolsRoot -CandidatePath (Join-Path $toolsRoot "cmake-$($cmake.version)-windows-x86_64")
$cmakeExe = Join-Path $installRoot "bin\cmake.exe"

if (Test-Path -LiteralPath $cmakeExe) {
    $versionOutput = @(& $cmakeExe --version)
    $versionExitCode = $LASTEXITCODE
    $versionLine = ([string]$versionOutput[0]).Trim()
    if ($versionExitCode -eq 0 -and $versionLine -eq "cmake version $($cmake.version)") {
        Write-Output $cmakeExe
        return
    }
    throw "Existing CMake tool does not match pinned version $($cmake.version)."
}

New-Item -ItemType Directory -Path $downloadRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath $archivePath)) {
    if ($Offline) {
        throw "Pinned CMake archive is missing and offline mode was requested."
    }
    $partialPath = "$archivePath.partial"
    Invoke-WebRequest -Uri ([string]$cmake.url) -OutFile $partialPath -UseBasicParsing
    Move-Item -LiteralPath $partialPath -Destination $archivePath
}

$actualDigest = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
$expectedDigest = ([string]$cmake.sha256).ToLowerInvariant()
if ($actualDigest -ne $expectedDigest) {
    throw "Pinned CMake archive digest mismatch. Expected '$expectedDigest', got '$actualDigest'."
}

$extractRoot = Assert-ContainedPath -BasePath $toolsRoot -CandidatePath (Join-Path $toolsRoot "_extract-$([Guid]::NewGuid().ToString('N'))")
New-Item -ItemType Directory -Path $extractRoot | Out-Null
try {
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractRoot
    $entries = @(Get-ChildItem -LiteralPath $extractRoot -Force)
    if ($entries.Count -ne 1 -or -not $entries[0].PSIsContainer) {
        throw "Unexpected CMake archive layout."
    }
    if (Test-Path -LiteralPath $installRoot) {
        throw "Pinned CMake install root already exists but is incomplete."
    }
    Move-Item -LiteralPath $entries[0].FullName -Destination $installRoot
} finally {
    if (Test-Path -LiteralPath $extractRoot) {
        Remove-Item -LiteralPath $extractRoot -Recurse -Force
    }
}

if (-not (Test-Path -LiteralPath $cmakeExe)) {
    throw "Pinned CMake executable was not found after extraction."
}

$installedVersionOutput = @(& $cmakeExe --version)
$installedVersionExitCode = $LASTEXITCODE
$installedVersion = ([string]$installedVersionOutput[0]).Trim()
if ($installedVersionExitCode -ne 0 -or $installedVersion -ne "cmake version $($cmake.version)") {
    throw "Extracted CMake tool does not match pinned version $($cmake.version)."
}

Write-Output $cmakeExe
