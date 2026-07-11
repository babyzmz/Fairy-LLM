[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OpenRouterKeyFile
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$profilesPath = Join-Path $repositoryRoot "config\openrouter-free.providers.json"
$desktopPath = Join-Path $repositoryRoot "desktop"

$resolvedKeyFile = Resolve-Path -LiteralPath $OpenRouterKeyFile -ErrorAction Stop
if (-not (Test-Path -LiteralPath $resolvedKeyFile -PathType Leaf)) {
    throw "OpenRouter key file is not a regular file"
}

$key = (Get-Content -LiteralPath $resolvedKeyFile -Raw).Trim()
if ($key.Length -lt 20) {
    throw "OpenRouter key file is empty or malformed"
}

$profiles = Get-Content -LiteralPath $profilesPath -Raw
$null = $profiles | ConvertFrom-Json -ErrorAction Stop
$secretReferences = '{"openrouter":"FAIRY_PROVIDER_SECRET_OPENROUTER"}'
$environmentNames = @(
    "FAIRY_PROVIDER_PROFILES_JSON",
    "FAIRY_PROVIDER_SECRET_REFS_JSON",
    "FAIRY_PROVIDER_SECRET_OPENROUTER"
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable(
        $name,
        [EnvironmentVariableTarget]::Process
    )
}

try {
    $env:FAIRY_PROVIDER_PROFILES_JSON = $profiles
    $env:FAIRY_PROVIDER_SECRET_REFS_JSON = $secretReferences
    $env:FAIRY_PROVIDER_SECRET_OPENROUTER = $key
    Push-Location -LiteralPath $desktopPath
    try {
        & npm run tauri -- dev
        if ($LASTEXITCODE -ne 0) {
            throw "Tauri development process exited with code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $previousEnvironment[$name],
            [EnvironmentVariableTarget]::Process
        )
    }
    $key = $null
}
