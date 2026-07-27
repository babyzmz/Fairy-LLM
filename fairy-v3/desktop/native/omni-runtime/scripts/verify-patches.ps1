[CmdletBinding()]
param(
    [switch]$ContractOnly,
    [string]$DependencyRoot,
    [string]$LockPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-CombinedPatchDigest {
    param(
        [Parameter(Mandatory = $true)][string]$RuntimeRoot,
        [Parameter(Mandatory = $true)]$PatchSet
    )

    $sha = [Security.Cryptography.IncrementalHash]::CreateHash(
        [Security.Cryptography.HashAlgorithmName]::SHA256
    )
    try {
        foreach ($patch in @($PatchSet.patches)) {
            $relative = ([string]$patch.path).Replace('\', '/')
            if ([IO.Path]::IsPathRooted($relative) -or $relative.Split('/') -contains "..") {
                throw "Patch path '$relative' is not a safe repository-relative path."
            }
            $fullPath = [IO.Path]::GetFullPath((Join-Path $RuntimeRoot $relative))
            $patchRoot = [IO.Path]::GetFullPath((Join-Path $RuntimeRoot "patches")).TrimEnd('\', '/')
            if (-not $fullPath.StartsWith("$patchRoot$([IO.Path]::DirectorySeparatorChar)", [StringComparison]::OrdinalIgnoreCase)) {
                throw "Patch path '$relative' escapes the patches directory."
            }
            if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
                throw "Patch '$relative' is missing."
            }

            $actualPatchDigest = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()
            $expectedPatchDigest = ([string]$patch.sha256).ToLowerInvariant()
            if ($actualPatchDigest -ne $expectedPatchDigest) {
                throw "Patch '$relative' digest mismatch."
            }

            $pathBytes = [Text.Encoding]::UTF8.GetBytes($relative)
            $contentBytes = [IO.File]::ReadAllBytes($fullPath)
            $nullByte = [byte[]]@(0)
            $sha.AppendData($pathBytes)
            $sha.AppendData($nullByte)
            $sha.AppendData($contentBytes)
            $sha.AppendData($nullByte)
        }
        return ([BitConverter]::ToString($sha.GetHashAndReset()) -replace "-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($LockPath)) {
    $LockPath = Join-Path $runtimeRoot "upstream.lock.json"
}
$LockPath = [IO.Path]::GetFullPath($LockPath)
$lock = Get-Content -LiteralPath $LockPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([int]$lock.schema_version -ne 1) {
    throw "Unsupported upstream lock schema '$($lock.schema_version)'."
}
if ([string]$lock.patch_set.digest_algorithm -ne "sha256-path-nul-content-nul-v1") {
    throw "Unsupported patch digest algorithm '$($lock.patch_set.digest_algorithm)'."
}

$combinedDigest = Get-CombinedPatchDigest -RuntimeRoot $runtimeRoot -PatchSet $lock.patch_set
$expectedCombinedDigest = ([string]$lock.patch_set.digest).ToLowerInvariant()
if ($combinedDigest -ne $expectedCombinedDigest) {
    throw "Patch-set digest mismatch. Expected '$expectedCombinedDigest', got '$combinedDigest'."
}

if (-not $ContractOnly) {
    $syncScript = Join-Path $PSScriptRoot "sync-upstream.ps1"
    $syncArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $syncScript, "-Offline")
    if (-not [string]::IsNullOrWhiteSpace($DependencyRoot)) {
        $syncArgs += @("-DependencyRoot", $DependencyRoot)
    }
    $syncArgs += @("-LockPath", $LockPath)
    $syncOutput = & powershell @syncArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Pinned upstream verification failed."
    }
    $syncOutput | Out-Host
}

[pscustomobject]@{
    schema_version = 1
    upstream_revision = [string]$lock.upstream.revision
    patch_count = @($lock.patch_set.patches).Count
    patch_set_digest = $combinedDigest
    contract_only = [bool]$ContractOnly
} | ConvertTo-Json -Compress
