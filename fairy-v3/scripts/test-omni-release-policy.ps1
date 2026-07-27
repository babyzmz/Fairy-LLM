[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "omni-release-policy.ps1")

$runtimeRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\desktop\native\omni-runtime"))
$contract = Resolve-OmniReleaseProbe -Profile contract -RuntimeRoot $runtimeRoot
if ($contract.model_root -ne $runtimeRoot -or $contract.require_model_probe) {
    throw "Contract staging policy changed unexpectedly."
}

$package = Resolve-OmniReleaseProbe -Profile production -RuntimeRoot $runtimeRoot
if ($package.model_root -ne $runtimeRoot -or $package.require_model_probe) {
    throw "Production packaging must not require separately downloaded model weights."
}

$scratch = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("fairy-omni-release-policy-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $scratch | Out-Null
try {
    $verified = Resolve-OmniReleaseProbe `
        -Profile production `
        -RuntimeRoot $runtimeRoot `
        -ModelRoot $scratch
    if ($verified.model_root -ne [System.IO.Path]::GetFullPath($scratch) -or
        -not $verified.require_model_probe) {
        throw "Explicit production model verification did not stay strict."
    }

    $threw = $false
    try {
        [void](Resolve-OmniReleaseProbe `
            -Profile production `
            -RuntimeRoot $runtimeRoot `
            -RequireModelProbe)
    }
    catch {
        $threw = $true
    }
    if (-not $threw) {
        throw "Release model verification accepted a missing model root."
    }
}
finally {
    $resolvedScratch = [System.IO.Path]::GetFullPath($scratch)
    $temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if (-not $resolvedScratch.StartsWith(
        $temporaryRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing policy fixture cleanup outside the temporary directory."
    }
    Remove-Item -LiteralPath $resolvedScratch -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Omni release packaging policy passed."
