[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Json {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $json = $Value | ConvertTo-Json -Depth 20
    [IO.File]::WriteAllText($Path, $json, [Text.UTF8Encoding]::new($false))
}

function Invoke-Toolchain {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(
            & powershell `
                -NoProfile `
                -ExecutionPolicy Bypass `
                -File $script:toolchainScript `
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

$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$toolchainScript = Join-Path $PSScriptRoot "cuda-toolchain.ps1"
$authoritativeLockPath = Join-Path $runtimeRoot "cuda-toolchain.lock.json"
$presetPath = Join-Path $runtimeRoot "CMakePresets.json"
$scratch = Join-Path ([IO.Path]::GetTempPath()) "fairy-cuda-toolchain-test-$([Guid]::NewGuid().ToString('N'))"
$fixtureRoot = Join-Path $scratch "fixture"
$fixtureLockPath = Join-Path $scratch "fixture-lock.json"

New-Item -ItemType Directory -Path $fixtureRoot -Force | Out-Null
try {
    $authoritative = Get-Content -LiteralPath $authoritativeLockPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    $fixtureLock = $authoritative | ConvertTo-Json -Depth 20 | ConvertFrom-Json

    foreach ($relative in @($fixtureLock.required_files)) {
        $path = Join-Path $fixtureRoot ([string]$relative).Replace("/", "\")
        New-Item -ItemType Directory -Path (Split-Path -Parent $path) -Force | Out-Null
        [IO.File]::WriteAllBytes($path, [byte[]](1, 2, 3))
    }
    $componentIndex = 0
    foreach ($component in @($fixtureLock.runtime_components)) {
        $bytes = [byte[]]@(
            [byte](10 + $componentIndex),
            [byte](20 + $componentIndex),
            [byte](30 + $componentIndex)
        )
        $path = Join-Path $fixtureRoot "Library\bin\$([string]$component.name)"
        [IO.File]::WriteAllBytes($path, $bytes)
        $component.bytes = [int64]$bytes.Length
        $component.sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        $componentIndex += 1
    }

    $metadataRoot = Join-Path $fixtureRoot "conda-meta"
    New-Item -ItemType Directory -Path $metadataRoot -Force | Out-Null
    foreach ($package in @($fixtureLock.packages)) {
        $metadata = [ordered]@{
            name = [string]$package.name
            version = [string]$package.version
            build = [string]$package.build
            license = [string]$package.license
        }
        Write-Json `
            -Value $metadata `
            -Path (Join-Path $metadataRoot "$([string]$package.name).json")
    }
    Write-Json -Value $fixtureLock -Path $fixtureLockPath

    $lockResult = Invoke-Toolchain -LockPath $authoritativeLockPath -ValidateLockOnly
    if ($lockResult.exit_code -ne 0 -or $lockResult.output -notmatch '"package_count":37') {
        throw "The authoritative CUDA lock did not validate: $($lockResult.output)"
    }

    $fixtureResult = Invoke-Toolchain `
        -ToolchainRoot $fixtureRoot `
        -LockPath $fixtureLockPath `
        -Offline
    if ($fixtureResult.exit_code -ne 0 -or $fixtureResult.output -notmatch '"schema_version":1') {
        throw "The controlled CUDA environment did not validate: $($fixtureResult.output)"
    }

    $invalidSchema = $fixtureLock | ConvertTo-Json -Depth 20 | ConvertFrom-Json
    $invalidSchema.schema_version = 2
    $invalidSchemaPath = Join-Path $scratch "invalid-schema.json"
    Write-Json -Value $invalidSchema -Path $invalidSchemaPath
    Assert-Failed `
        -Result (Invoke-Toolchain -LockPath $invalidSchemaPath -ValidateLockOnly) `
        -Pattern "schema or platform"

    $unicodeRoot = Join-Path $scratch ([char]0x00E9)
    Assert-Failed `
        -Result (Invoke-Toolchain `
            -ToolchainRoot $unicodeRoot `
            -LockPath $fixtureLockPath `
            -Offline) `
        -Pattern "ASCII-safe"

    $missingRoot = Join-Path $scratch "missing"
    Assert-Failed `
        -Result (Invoke-Toolchain `
            -ToolchainRoot $missingRoot `
            -LockPath $fixtureLockPath `
            -Offline) `
        -Pattern "cache is incomplete"

    $nvccPath = Join-Path $fixtureRoot "Library\bin\nvcc.exe"
    $savedNvcc = [IO.File]::ReadAllBytes($nvccPath)
    Remove-Item -LiteralPath $nvccPath -Force
    Assert-Failed `
        -Result (Invoke-Toolchain `
            -ToolchainRoot $fixtureRoot `
            -LockPath $fixtureLockPath `
            -Offline) `
        -Pattern "nvcc.exe.*missing"
    [IO.File]::WriteAllBytes($nvccPath, $savedNvcc)

    $ninjaMetadataPath = Join-Path $metadataRoot "ninja.json"
    $ninjaMetadata = Get-Content -LiteralPath $ninjaMetadataPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    $ninjaMetadata.version = "0.0.0"
    Write-Json -Value $ninjaMetadata -Path $ninjaMetadataPath
    Assert-Failed `
        -Result (Invoke-Toolchain `
            -ToolchainRoot $fixtureRoot `
            -LockPath $fixtureLockPath `
            -Offline) `
        -Pattern "ninja.*does not match"
    $ninjaMetadata.version = "1.13.2"
    Write-Json -Value $ninjaMetadata -Path $ninjaMetadataPath

    $cublasPath = Join-Path $fixtureRoot "Library\bin\cublas64_13.dll"
    [IO.File]::WriteAllBytes($cublasPath, [byte[]](99))
    Assert-Failed `
        -Result (Invoke-Toolchain `
            -ToolchainRoot $fixtureRoot `
            -LockPath $fixtureLockPath `
            -Offline) `
        -Pattern "cublas64_13.dll.*integrity"

    $presets = Get-Content -LiteralPath $presetPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $presetByName = @{}
    foreach ($preset in @($presets.configurePresets)) {
        $presetByName[[string]$preset.name] = $preset
    }
    if (
        [string]$presetByName["contract"].generator -ne "Visual Studio 17 2022" -or
        [string]$presetByName["upstream-cpu"].generator -ne "Visual Studio 17 2022" -or
        [string]$presetByName["production-cuda"].generator -ne "Ninja Multi-Config" -or
        $null -ne $presetByName["production-cuda"].PSObject.Properties["architecture"]
    ) {
        throw "The CMake generator split does not match the portable CUDA design."
    }
} finally {
    if (Test-Path -LiteralPath $scratch) {
        Remove-Item -LiteralPath $scratch -Recurse -Force
    }
}

Write-Output "CUDA toolchain tests passed"
