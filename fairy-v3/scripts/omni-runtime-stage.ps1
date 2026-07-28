[CmdletBinding()]
param(
    [ValidateSet("contract", "production-cuda")]
    [string]$Profile,
    [string]$Executable,
    [string]$StageRoot,
    [string]$ToolchainRoot,
    [string]$CudaLockPath,
    [string]$RuntimeCompatibility,
    [string]$UpstreamRevision,
    [string]$PatchSetDigest,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-ChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Candidate
    )

    $resolvedParent = [IO.Path]::GetFullPath($Parent).TrimEnd("\", "/")
    $resolvedCandidate = [IO.Path]::GetFullPath($Candidate).TrimEnd("\", "/")
    if (
        -not $resolvedCandidate.StartsWith(
            "$resolvedParent$([IO.Path]::DirectorySeparatorChar)",
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "The Omni staging path escapes its governed parent."
    }
    return $resolvedCandidate
}

function Assert-Stage {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ExpectedProfile
    )

    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "The Omni runtime stage is missing."
    }
    $manifestPath = Join-Path $Root "runtime-components.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "The Omni runtime component manifest is missing."
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    if (
        [int]$manifest.schema_version -ne 1 -or
        [string]$manifest.build_profile -ne $ExpectedProfile -or
        [string]::IsNullOrWhiteSpace([string]$manifest.runtime_compatibility) -or
        [string]$manifest.upstream_revision -cnotmatch '^[0-9a-f]{40}$' -or
        [string]$manifest.patch_set_digest -cnotmatch '^[0-9a-f]{64}$'
    ) {
        throw "The Omni runtime component manifest identity is invalid."
    }

    $components = @($manifest.components)
    $expectedNames = @("build-profile.txt", "fairy-omni-runtime.exe")
    if ($ExpectedProfile -eq "production-cuda") {
        $expectedNames += @("cublas64_13.dll", "cublasLt64_13.dll")
    }
    $actualNames = @($components | ForEach-Object { [string]$_.name })
    if (
        $components.Count -ne $expectedNames.Count -or
        @(Compare-Object $actualNames $expectedNames).Count -ne 0
    ) {
        throw "The Omni runtime component set is not exact."
    }

    foreach ($component in $components) {
        $name = [string]$component.name
        if (
            $name -cnotmatch '^[a-zA-Z0-9][a-zA-Z0-9._-]*$' -or
            [int64]$component.bytes -le 0 -or
            [string]$component.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            [string]::IsNullOrWhiteSpace([string]$component.package) -or
            [string]::IsNullOrWhiteSpace([string]$component.version) -or
            [string]::IsNullOrWhiteSpace([string]$component.license)
        ) {
            throw "The Omni runtime component metadata is invalid."
        }
        $path = Join-Path $Root $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "The Omni runtime component '$name' is missing."
        }
        $item = Get-Item -LiteralPath $path
        if (
            [int64]$item.Length -ne [int64]$component.bytes -or
            (Get-FileSha256 -Path $path) -ne [string]$component.sha256
        ) {
            throw "The Omni runtime component '$name' failed integrity validation."
        }
    }

    $profileText = Get-Content -LiteralPath (Join-Path $Root "build-profile.txt") -Raw -Encoding UTF8
    if ($profileText.Trim() -ne $ExpectedProfile) {
        throw "The staged Omni runtime profile does not match its manifest."
    }

    $allowedNames = @($expectedNames) + @("runtime-components.json", ".gitkeep")
    $normalizedRoot = [IO.Path]::GetFullPath($Root).TrimEnd("\", "/")
    foreach ($file in @(Get-ChildItem -LiteralPath $Root -Recurse -File)) {
        $resolvedFile = [IO.Path]::GetFullPath($file.FullName)
        if (
            -not $resolvedFile.StartsWith(
                "$normalizedRoot$([IO.Path]::DirectorySeparatorChar)",
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "The Omni runtime stage file escapes its root."
        }
        $relative = $resolvedFile.Substring($normalizedRoot.Length + 1).Replace("\", "/")
        if ($relative.Contains("/") -or $relative -notin $allowedNames) {
            throw "The Omni runtime stage contains an unknown file: '$relative'."
        }
        if ($file.Extension -in @(".gguf", ".env", ".db", ".log", ".partial")) {
            throw "The Omni runtime stage contains forbidden content."
        }
    }

    return $manifest
}

$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runtimeRoot = Join-Path $projectRoot "desktop\native\omni-runtime"
if ([string]::IsNullOrWhiteSpace($StageRoot)) {
    $StageRoot = Join-Path $projectRoot "desktop\src-tauri\runtime\omni"
}
$resolvedStageRoot = [IO.Path]::GetFullPath($StageRoot)

if ($ValidateOnly) {
    if ([string]::IsNullOrWhiteSpace($Profile)) {
        throw "ValidateOnly requires the expected Omni profile."
    }
    Assert-Stage -Root $resolvedStageRoot -ExpectedProfile $Profile | ConvertTo-Json -Depth 6 -Compress
    return
}

if (
    [string]::IsNullOrWhiteSpace($Profile) -or
    [string]::IsNullOrWhiteSpace($Executable) -or
    [string]::IsNullOrWhiteSpace($RuntimeCompatibility) -or
    [string]::IsNullOrWhiteSpace($UpstreamRevision) -or
    [string]::IsNullOrWhiteSpace($PatchSetDigest)
) {
    throw "Omni staging requires a complete runtime identity."
}
$resolvedExecutable = [IO.Path]::GetFullPath($Executable)
if (-not (Test-Path -LiteralPath $resolvedExecutable -PathType Leaf)) {
    throw "The verified Omni executable is missing."
}

$stageParent = Split-Path -Parent $resolvedStageRoot
New-Item -ItemType Directory -Path $stageParent -Force | Out-Null
$transactionId = [Guid]::NewGuid().ToString("N")
$prepareRoot = Assert-ChildPath `
    -Parent $stageParent `
    -Candidate "$resolvedStageRoot.prepare-$transactionId"
$backupRoot = Assert-ChildPath `
    -Parent $stageParent `
    -Candidate "$resolvedStageRoot.backup-$transactionId"
$swapped = $false

try {
    New-Item -ItemType Directory -Path $prepareRoot | Out-Null
    $gitkeep = Join-Path $resolvedStageRoot ".gitkeep"
    if (Test-Path -LiteralPath $gitkeep -PathType Leaf) {
        Copy-Item -LiteralPath $gitkeep -Destination (Join-Path $prepareRoot ".gitkeep")
    }

    $stagedExecutable = Join-Path $prepareRoot "fairy-omni-runtime.exe"
    Copy-Item -LiteralPath $resolvedExecutable -Destination $stagedExecutable
    [IO.File]::WriteAllText(
        (Join-Path $prepareRoot "build-profile.txt"),
        "$Profile`n",
        [Text.UTF8Encoding]::new($false)
    )

    $components = @(
        [ordered]@{
            name = "fairy-omni-runtime.exe"
            package = "fairy-omni-runtime"
            version = $RuntimeCompatibility
            license = "Fairy-and-third-party-notices"
            bytes = [int64](Get-Item -LiteralPath $stagedExecutable).Length
            sha256 = Get-FileSha256 -Path $stagedExecutable
        },
        [ordered]@{
            name = "build-profile.txt"
            package = "fairy-omni-runtime"
            version = $Profile
            license = "Fairy-and-third-party-notices"
            bytes = [int64](Get-Item -LiteralPath (Join-Path $prepareRoot "build-profile.txt")).Length
            sha256 = Get-FileSha256 -Path (Join-Path $prepareRoot "build-profile.txt")
        }
    )

    if ($Profile -eq "production-cuda") {
        $toolchainArguments = @(
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            (Join-Path $runtimeRoot "scripts\cuda-toolchain.ps1"),
            "-Offline"
        )
        if (-not [string]::IsNullOrWhiteSpace($ToolchainRoot)) {
            $toolchainArguments += @("-ToolchainRoot", $ToolchainRoot)
        }
        if (-not [string]::IsNullOrWhiteSpace($CudaLockPath)) {
            $toolchainArguments += @("-LockPath", $CudaLockPath)
        }
        $toolchainOutput = @(& powershell @toolchainArguments)
        if ($LASTEXITCODE -ne 0 -or $toolchainOutput.Count -eq 0) {
            throw "The verified CUDA toolchain is unavailable for staging."
        }
        $toolchain = [string]$toolchainOutput[-1] | ConvertFrom-Json
        foreach ($component in @($toolchain.runtime_components)) {
            $destination = Join-Path $prepareRoot ([string]$component.name)
            Copy-Item -LiteralPath ([string]$component.source) -Destination $destination
            if (
                [int64](Get-Item -LiteralPath $destination).Length -ne [int64]$component.bytes -or
                (Get-FileSha256 -Path $destination) -ne [string]$component.sha256
            ) {
                throw "The copied CUDA runtime component failed integrity validation."
            }
            $components += [ordered]@{
                name = [string]$component.name
                package = [string]$component.package
                version = [string]$component.version
                license = [string]$component.license
                bytes = [int64]$component.bytes
                sha256 = [string]$component.sha256
            }
        }
    }

    $manifest = [ordered]@{
        schema_version = 1
        build_profile = $Profile
        runtime_compatibility = $RuntimeCompatibility
        upstream_revision = $UpstreamRevision
        patch_set_digest = $PatchSetDigest
        components = @($components | Sort-Object { [string]$_.name })
    }
    $manifestJson = $manifest | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText(
        (Join-Path $prepareRoot "runtime-components.json"),
        "$manifestJson`n",
        [Text.UTF8Encoding]::new($false)
    )
    Assert-Stage -Root $prepareRoot -ExpectedProfile $Profile | Out-Null

    if (Test-Path -LiteralPath $resolvedStageRoot) {
        Move-Item -LiteralPath $resolvedStageRoot -Destination $backupRoot
    }
    try {
        Move-Item -LiteralPath $prepareRoot -Destination $resolvedStageRoot
        $swapped = $true
    } catch {
        if (
            -not (Test-Path -LiteralPath $resolvedStageRoot) -and
            (Test-Path -LiteralPath $backupRoot)
        ) {
            Move-Item -LiteralPath $backupRoot -Destination $resolvedStageRoot
        }
        throw
    }
    Assert-Stage -Root $resolvedStageRoot -ExpectedProfile $Profile | Out-Null
    if (Test-Path -LiteralPath $backupRoot) {
        Remove-Item -LiteralPath $backupRoot -Recurse -Force
    }
} finally {
    if (Test-Path -LiteralPath $prepareRoot) {
        Remove-Item -LiteralPath $prepareRoot -Recurse -Force
    }
    if (-not $swapped -and (Test-Path -LiteralPath $backupRoot)) {
        if (-not (Test-Path -LiteralPath $resolvedStageRoot)) {
            Move-Item -LiteralPath $backupRoot -Destination $resolvedStageRoot
        } else {
            Remove-Item -LiteralPath $backupRoot -Recurse -Force
        }
    }
}

Assert-Stage -Root $resolvedStageRoot -ExpectedProfile $Profile | ConvertTo-Json -Depth 6 -Compress
