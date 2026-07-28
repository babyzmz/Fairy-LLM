Set-StrictMode -Version Latest

function Remove-VoiceReleaseCaches {
    param(
        [Parameter(Mandatory)]
        [string]$Destination
    )

    $resolvedDestination = [System.IO.Path]::GetFullPath($Destination).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar
    )
    if (-not (Test-Path -LiteralPath $resolvedDestination -PathType Container)) {
        throw "The generated Voice runtime destination does not exist."
    }
    $destinationPrefix = $resolvedDestination + [System.IO.Path]::DirectorySeparatorChar
    $cacheDirectories = @(
        Get-ChildItem -LiteralPath $resolvedDestination -Recurse -Directory -Force |
            Where-Object { $_.Name -eq "__pycache__" } |
            Sort-Object { $_.FullName.Length } -Descending
    )
    foreach ($cacheDirectory in $cacheDirectories) {
        $resolvedCache = [System.IO.Path]::GetFullPath($cacheDirectory.FullName)
        if (-not $resolvedCache.StartsWith(
            $destinationPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Refusing to remove a Voice cache outside the generated runtime."
        }
        Remove-Item -LiteralPath $resolvedCache -Recurse -Force
    }
    return $cacheDirectories.Count
}
