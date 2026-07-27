Set-StrictMode -Version Latest

function Resolve-OmniReleaseProbe {
    param(
        [Parameter(Mandatory)]
        [ValidateSet("contract", "production")]
        [string]$Profile,
        [string]$ModelRoot,
        [Parameter(Mandatory)]
        [string]$RuntimeRoot,
        [switch]$RequireModelProbe
    )

    $resolvedRuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
    if ($Profile -eq "contract") {
        return [pscustomobject]@{
            model_root = $resolvedRuntimeRoot
            require_model_probe = $false
        }
    }
    if ([string]::IsNullOrWhiteSpace($ModelRoot)) {
        if ($RequireModelProbe) {
            throw "A verified three-file model root is required for the release model probe."
        }
        return [pscustomobject]@{
            model_root = $resolvedRuntimeRoot
            require_model_probe = $false
        }
    }
    $resolvedModelRoot = [System.IO.Path]::GetFullPath($ModelRoot)
    if (-not (Test-Path -LiteralPath $resolvedModelRoot -PathType Container)) {
        throw "The production Omni model root does not exist."
    }
    return [pscustomobject]@{
        model_root = $resolvedModelRoot
        require_model_probe = $true
    }
}
