[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Json {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string]$Path
    )

    [IO.File]::WriteAllText(
        $Path,
        ($Value | ConvertTo-Json -Depth 20),
        [Text.UTF8Encoding]::new($false)
    )
}

function Invoke-Stage {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(
            & powershell `
                -NoProfile `
                -ExecutionPolicy Bypass `
                -File $script:stageScript `
                @Arguments 2>&1
        )
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    return [pscustomobject]@{
        exit_code = $exitCode
        output = $output -join "`n"
    }
}

function Assert-Failed {
    param(
        [Parameter(Mandatory = $true)]$Result,
        [Parameter(Mandatory = $true)][string]$Pattern
    )

    if ([int]$Result.exit_code -eq 0 -or [string]$Result.output -notmatch $Pattern) {
        throw "Expected failure matching '$Pattern', got exit $($Result.exit_code): $($Result.output)"
    }
}

$root = Split-Path -Parent $PSScriptRoot
$stageScript = Join-Path $PSScriptRoot "omni-runtime-stage.ps1"
$authoritativeCudaLock = Join-Path $root "desktop\native\omni-runtime\cuda-toolchain.lock.json"
$scratch = Join-Path ([IO.Path]::GetTempPath()) "fairy-omni-stage-test-$([Guid]::NewGuid().ToString('N'))"
$stageRoot = Join-Path $scratch "stage"
$executable = Join-Path $scratch "fairy-omni-runtime.exe"
$fixtureToolchain = Join-Path $scratch "cuda"
$fixtureLockPath = Join-Path $scratch "cuda-lock.json"
$runtimeCompatibility = "fairy-omni-runtime-v1"
$upstreamRevision = "74699a53df6ca0f4947ff37066f851532c20b12d"
$patchSetDigest = "72b89b34a81b2a49abb5079bd411fc6676650e750873e2ec51cb796f49a597bd"

New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
try {
    [IO.File]::WriteAllText(
        (Join-Path $stageRoot ".gitkeep"),
        "`n",
        [Text.UTF8Encoding]::new($false)
    )
    [IO.File]::WriteAllBytes($executable, [byte[]](77, 90, 1, 2, 3))

    $contract = Invoke-Stage `
        -Profile contract `
        -Executable $executable `
        -StageRoot $stageRoot `
        -RuntimeCompatibility $runtimeCompatibility `
        -UpstreamRevision $upstreamRevision `
        -PatchSetDigest $patchSetDigest
    if ($contract.exit_code -ne 0 -or $contract.output -notmatch '"build_profile":"contract"') {
        throw "The controlled contract runtime did not stage: $($contract.output)"
    }
    foreach ($name in @(
        ".gitkeep",
        "build-profile.txt",
        "fairy-omni-runtime.exe",
        "runtime-components.json"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $stageRoot $name) -PathType Leaf)) {
            throw "The contract stage is missing '$name'."
        }
    }
    $contractHash = (Get-FileHash `
        -LiteralPath (Join-Path $stageRoot "fairy-omni-runtime.exe") `
        -Algorithm SHA256).Hash

    [IO.File]::WriteAllBytes(
        (Join-Path $stageRoot "fairy-omni-runtime.exe"),
        [byte[]](99)
    )
    Assert-Failed `
        -Result (Invoke-Stage -Profile contract -StageRoot $stageRoot -ValidateOnly) `
        -Pattern "integrity"

    $contract = Invoke-Stage `
        -Profile contract `
        -Executable $executable `
        -StageRoot $stageRoot `
        -RuntimeCompatibility $runtimeCompatibility `
        -UpstreamRevision $upstreamRevision `
        -PatchSetDigest $patchSetDigest
    if ($contract.exit_code -ne 0) {
        throw "The controlled contract stage could not be restored."
    }
    [IO.File]::WriteAllText(
        (Join-Path $stageRoot "unknown.dll"),
        "unknown",
        [Text.UTF8Encoding]::new($false)
    )
    Assert-Failed `
        -Result (Invoke-Stage -Profile contract -StageRoot $stageRoot -ValidateOnly) `
        -Pattern "unknown file"
    Remove-Item -LiteralPath (Join-Path $stageRoot "unknown.dll") -Force

    $missingToolchain = Join-Path $scratch "missing-toolchain"
    Assert-Failed `
        -Result (Invoke-Stage `
            -Profile production-cuda `
            -Executable $executable `
            -StageRoot $stageRoot `
            -ToolchainRoot $missingToolchain `
            -RuntimeCompatibility $runtimeCompatibility `
            -UpstreamRevision $upstreamRevision `
            -PatchSetDigest $patchSetDigest) `
        -Pattern "cache is incomplete"
    if (
        (Get-FileHash `
            -LiteralPath (Join-Path $stageRoot "fairy-omni-runtime.exe") `
            -Algorithm SHA256).Hash -ne $contractHash -or
        (Get-Content -LiteralPath (Join-Path $stageRoot "build-profile.txt") -Raw).Trim() -ne "contract"
    ) {
        throw "A failed production preparation changed the verified previous stage."
    }

    $fixtureLock = Get-Content -LiteralPath $authoritativeCudaLock -Raw -Encoding UTF8 |
        ConvertFrom-Json
    foreach ($relative in @($fixtureLock.required_files)) {
        $path = Join-Path $fixtureToolchain ([string]$relative).Replace("/", "\")
        New-Item -ItemType Directory -Path (Split-Path -Parent $path) -Force | Out-Null
        [IO.File]::WriteAllBytes($path, [byte[]](1, 2, 3))
    }
    $componentIndex = 0
    foreach ($component in @($fixtureLock.runtime_components)) {
        $bytes = [byte[]]@(
            [byte](40 + $componentIndex),
            [byte](50 + $componentIndex),
            [byte](60 + $componentIndex)
        )
        $path = Join-Path $fixtureToolchain "Library\bin\$([string]$component.name)"
        [IO.File]::WriteAllBytes($path, $bytes)
        $component.bytes = [int64]$bytes.Length
        $component.sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        $componentIndex += 1
    }
    $metadataRoot = Join-Path $fixtureToolchain "conda-meta"
    New-Item -ItemType Directory -Path $metadataRoot -Force | Out-Null
    foreach ($package in @($fixtureLock.packages)) {
        Write-Json `
            -Value ([ordered]@{
                name = [string]$package.name
                version = [string]$package.version
                build = [string]$package.build
                license = [string]$package.license
            }) `
            -Path (Join-Path $metadataRoot "$([string]$package.name).json")
    }
    Write-Json -Value $fixtureLock -Path $fixtureLockPath

    $production = Invoke-Stage `
        -Profile production-cuda `
        -Executable $executable `
        -StageRoot $stageRoot `
        -ToolchainRoot $fixtureToolchain `
        -CudaLockPath $fixtureLockPath `
        -RuntimeCompatibility $runtimeCompatibility `
        -UpstreamRevision $upstreamRevision `
        -PatchSetDigest $patchSetDigest
    if (
        $production.exit_code -ne 0 -or
        $production.output -notmatch '"build_profile":"production-cuda"'
    ) {
        throw "The controlled production runtime did not stage: $($production.output)"
    }
    $manifest = Get-Content `
        -LiteralPath (Join-Path $stageRoot "runtime-components.json") `
        -Raw `
        -Encoding UTF8 |
        ConvertFrom-Json
    if (
        @($manifest.components).Count -ne 4 -or
        -not (Test-Path -LiteralPath (Join-Path $stageRoot "cublas64_13.dll")) -or
        -not (Test-Path -LiteralPath (Join-Path $stageRoot "cublasLt64_13.dll"))
    ) {
        throw "The production stage does not contain the exact CUDA runtime set."
    }

    [IO.File]::WriteAllBytes((Join-Path $stageRoot "cublas64_13.dll"), [byte[]](1))
    Assert-Failed `
        -Result (Invoke-Stage -Profile production-cuda -StageRoot $stageRoot -ValidateOnly) `
        -Pattern "integrity"
} finally {
    if (Test-Path -LiteralPath $scratch) {
        Remove-Item -LiteralPath $scratch -Recurse -Force
    }
}

Write-Output "Omni runtime staging tests passed"
