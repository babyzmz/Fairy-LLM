[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "voice-release-licenses.ps1")

$scratch = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("fairy-voice-license-fixture-" + [guid]::NewGuid().ToString("N"))
$source = Join-Path $scratch "CosyVoice"
$destination = Join-Path $scratch "runtime"
New-Item -ItemType Directory -Force `
    -Path (Join-Path $source "third_party\Matcha-TTS"), $destination | Out-Null
try {
    Set-Content -LiteralPath (Join-Path $source "LICENSE") -Value "CosyVoice license"
    Set-Content `
        -LiteralPath (Join-Path $source "third_party\Matcha-TTS\LICENSE") `
        -Value "Matcha license"
    $licenseRoot = Copy-VoiceReleaseLicenses `
        -CosyVoiceRoot $source `
        -Destination $destination
    foreach ($name in @("CosyVoice-LICENSE.txt", "Matcha-TTS-LICENSE.txt")) {
        if (-not (Test-Path -LiteralPath (Join-Path $licenseRoot $name) -PathType Leaf)) {
            throw "Voice license fixture did not copy $name."
        }
    }

    Remove-Item -LiteralPath (Join-Path $source "LICENSE") -Force
    $threw = $false
    try {
        [void](Copy-VoiceReleaseLicenses `
            -CosyVoiceRoot $source `
            -Destination $destination)
    }
    catch {
        $threw = $true
    }
    if (-not $threw) {
        throw "Voice license staging accepted a missing upstream license."
    }
}
finally {
    $resolvedScratch = [System.IO.Path]::GetFullPath($scratch)
    $temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if (-not $resolvedScratch.StartsWith(
        $temporaryRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing Voice license fixture cleanup outside the temporary directory."
    }
    Remove-Item -LiteralPath $resolvedScratch -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Voice release licenses passed."
