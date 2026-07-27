[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-Throws {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [Parameter(Mandatory = $true)][string]$Pattern
    )

    try {
        & $Action
    } catch {
        if ($_.Exception.Message -notmatch $Pattern) {
            throw "Expected error matching '$Pattern', got '$($_.Exception.Message)'."
        }
        return
    }
    throw "Expected action to throw an error matching '$Pattern'."
}

function Invoke-Git {
    param([string]$Directory, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & git -c "safe.directory=$Directory" -C $Directory @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed in '$Directory'."
    }
}

$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$syncScript = Join-Path $PSScriptRoot "sync-upstream.ps1"
$verifyScript = Join-Path $PSScriptRoot "verify-patches.ps1"
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) "fairy-omni-source-lock-$([Guid]::NewGuid().ToString('N'))"
$fakeOrigin = Join-Path $tempRoot "origin"
$dependencyRoot = Join-Path $runtimeRoot "_deps\source-lock-test"
$authoritativeLockPath = Join-Path $runtimeRoot "upstream.lock.json"
$lockPath = Join-Path $tempRoot "upstream.lock.json"
$originalLock = Get-Content -LiteralPath $authoritativeLockPath -Raw -Encoding UTF8

New-Item -ItemType Directory -Path $fakeOrigin -Force | Out-Null
try {
    Invoke-Git -Directory $fakeOrigin init --quiet
    Invoke-Git -Directory $fakeOrigin config user.name "Fairy Source Lock Test"
    Invoke-Git -Directory $fakeOrigin config user.email "source-lock-test@localhost.invalid"
    Set-Content -LiteralPath (Join-Path $fakeOrigin "README.md") -Value "fixture" -Encoding UTF8
    Invoke-Git -Directory $fakeOrigin add README.md
    Invoke-Git -Directory $fakeOrigin commit --quiet -m fixture
    $revision = (& git -c "safe.directory=$fakeOrigin" -C $fakeOrigin rev-parse HEAD).Trim()

    $testLock = $originalLock | ConvertFrom-Json
    $testLock.upstream.repository = $fakeOrigin
    $testLock.upstream.revision = $revision
    $testLock | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $lockPath -Encoding UTF8

    & $syncScript -DependencyRoot $dependencyRoot -LockPath $lockPath | Out-Null

    Set-Content -LiteralPath (Join-Path $dependencyRoot "dirty.txt") -Value "dirty" -Encoding UTF8
    Assert-Throws -Pattern "dirty" -Action {
        & $syncScript -DependencyRoot $dependencyRoot -LockPath $lockPath -Offline | Out-Null
    }
    Remove-Item -LiteralPath (Join-Path $dependencyRoot "dirty.txt") -Force

    $testLock.upstream.revision = "0000000000000000000000000000000000000000"
    $testLock | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $lockPath -Encoding UTF8
    Assert-Throws -Pattern "unavailable" -Action {
        & $syncScript -DependencyRoot $dependencyRoot -LockPath $lockPath -Offline | Out-Null
    }

    $testLock.upstream.revision = $revision
    $testLock.patch_set.digest = "0000000000000000000000000000000000000000000000000000000000000000"
    $testLock | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $lockPath -Encoding UTF8
    Assert-Throws -Pattern "Patch-set digest mismatch" -Action {
        & $verifyScript -ContractOnly -LockPath $lockPath | Out-Null
    }

    $testLock.patch_set.digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    $testLock | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $lockPath -Encoding UTF8
    $escapedDependency = Join-Path $runtimeRoot "outside-source-lock-test"
    Assert-Throws -Pattern "escapes the allowed root" -Action {
        & $syncScript -DependencyRoot $escapedDependency -LockPath $lockPath -Offline | Out-Null
    }
} finally {
    if (Test-Path -LiteralPath $dependencyRoot) {
        Remove-Item -LiteralPath $dependencyRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}

Write-Output "source-lock tests passed"
