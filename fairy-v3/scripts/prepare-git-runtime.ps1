[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$version = "2.55.0.2"
$expectedHash = "e3ea2944cea4b3fabcd69c7c1669ef69b1b66c05ac7806d81224d0abad2dec31"
$archiveName = "MinGit-${version}-64-bit.zip"
$downloadUrl = "https://github.com/git-for-windows/git/releases/download/v2.55.0.windows.2/$archiveName"
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root "desktop\src-tauri\runtime\git"
$cache = Join-Path ([System.IO.Path]::GetTempPath()) "fairy-v3-build-cache"
$archive = Join-Path $cache $archiveName

New-Item -ItemType Directory -Force -Path $cache | Out-Null
if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
    Invoke-WebRequest -UseBasicParsing -Uri $downloadUrl -OutFile $archive
}
$actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) {
    Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
    throw "MinGit archive hash mismatch"
}

Remove-Item -LiteralPath $runtime -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
Expand-Archive -LiteralPath $archive -DestinationPath $runtime -Force

$git = Join-Path $runtime "cmd\git.exe"
if (-not (Test-Path -LiteralPath $git -PathType Leaf)) {
    throw "MinGit runtime does not contain cmd\git.exe"
}
$versionOutput = & $git --version
if ($LASTEXITCODE -ne 0 -or $versionOutput -notmatch '^git version 2\.55\.0') {
    throw "MinGit runtime version probe failed"
}
Write-Host "Prepared $versionOutput at $runtime"
