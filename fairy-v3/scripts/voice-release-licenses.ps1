Set-StrictMode -Version Latest

function Copy-VoiceReleaseLicenses {
    param(
        [Parameter(Mandatory)]
        [string]$CosyVoiceRoot,
        [Parameter(Mandatory)]
        [string]$Destination
    )

    $resolvedSource = [System.IO.Path]::GetFullPath($CosyVoiceRoot)
    $resolvedDestination = [System.IO.Path]::GetFullPath($Destination)
    if (-not (Test-Path -LiteralPath $resolvedDestination -PathType Container)) {
        throw "The generated Voice runtime destination does not exist."
    }
    $sources = [ordered]@{
        "CosyVoice-LICENSE.txt" = Join-Path $resolvedSource "LICENSE"
        "Matcha-TTS-LICENSE.txt" = Join-Path $resolvedSource "third_party\Matcha-TTS\LICENSE"
    }
    foreach ($source in $sources.Values) {
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "A required Voice runtime license is missing: $source"
        }
    }

    $licenseRoot = Join-Path $resolvedDestination "THIRD_PARTY_LICENSES"
    New-Item -ItemType Directory -Force -Path $licenseRoot | Out-Null
    foreach ($entry in $sources.GetEnumerator()) {
        Copy-Item -LiteralPath $entry.Value -Destination (Join-Path $licenseRoot $entry.Key) -Force
    }
    return $licenseRoot
}
