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

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )

    & git -c "safe.directory=$WorkingDirectory" -C $WorkingDirectory @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed in '$WorkingDirectory'."
    }
}

function Assert-ContainedPath {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string]$CandidatePath
    )

    $base = [IO.Path]::GetFullPath($BasePath).TrimEnd('\', '/')
    $candidate = [IO.Path]::GetFullPath($CandidatePath).TrimEnd('\', '/')
    if (-not $candidate.StartsWith(
        "$base$([IO.Path]::DirectorySeparatorChar)",
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Patched source path '$candidate' escapes '$base'."
    }
    return $candidate
}

function Assert-NoReparsePoint {
    param([Parameter(Mandatory = $true)][string]$Path)

    $current = [IO.Path]::GetFullPath($Path)
    while (Test-Path -LiteralPath $current) {
        $item = Get-Item -LiteralPath $current -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing reparse-point path '$current'."
        }
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrEmpty($parent) -or $parent -eq $current) {
            break
        }
        $current = $parent
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

$patchedSource = $null
if (-not $ContractOnly) {
    $syncScript = Join-Path $PSScriptRoot "sync-upstream.ps1"
    $syncArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $syncScript, "-Offline")
    if (-not [string]::IsNullOrWhiteSpace($DependencyRoot)) {
        $syncArgs += @("-DependencyRoot", $DependencyRoot)
    }
    $syncArgs += @("-LockPath", $LockPath)
    $syncOutput = @(& powershell @syncArgs)
    if ($LASTEXITCODE -ne 0) {
        throw "Pinned upstream verification failed."
    }
    $syncResult = $syncOutput[-1] | ConvertFrom-Json
    $sourceRoot = [IO.Path]::GetFullPath([string]$syncResult.source_root)
    $dependencyBase = [IO.Path]::GetFullPath((Join-Path $runtimeRoot "_deps"))
    $patchedSource = Assert-ContainedPath `
        -BasePath $dependencyBase `
        -CandidatePath (Join-Path $dependencyBase "llama.cpp-omni-patched")
    Assert-NoReparsePoint -Path $sourceRoot
    Assert-NoReparsePoint -Path $patchedSource
    if (Test-Path -LiteralPath $patchedSource) {
        Remove-Item -LiteralPath $patchedSource -Recurse -Force
    }

    & git clone --quiet --no-hardlinks $sourceRoot $patchedSource
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to create the local patched source tree."
    }
    Invoke-Git -WorkingDirectory $patchedSource checkout --quiet --detach ([string]$lock.upstream.revision)
    foreach ($patch in @($lock.patch_set.patches)) {
        $patchPath = Join-Path $runtimeRoot ([string]$patch.path)
        Invoke-Git -WorkingDirectory $patchedSource apply --check $patchPath
        Invoke-Git -WorkingDirectory $patchedSource apply $patchPath
    }
    Invoke-Git -WorkingDirectory $patchedSource diff --check
}

[pscustomobject]@{
    schema_version = 1
    upstream_revision = [string]$lock.upstream.revision
    patch_count = @($lock.patch_set.patches).Count
    patch_set_digest = $combinedDigest
    contract_only = [bool]$ContractOnly
    patched_source_root = $patchedSource
} | ConvertTo-Json -Compress
