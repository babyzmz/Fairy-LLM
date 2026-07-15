[CmdletBinding()]
param(
    [string]$OpenRouterKeyFile
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function New-DesktopPathAlias {
    param([Parameter(Mandatory = $true)][string]$Root)

    if ($Root -cmatch '^[\x00-\x7F]+$' -and -not $Root.Contains("~")) {
        return [PSCustomObject]@{ Root = $Root; Drive = $null }
    }

    $subst = Get-Command subst.exe -ErrorAction Stop
    foreach ($letter in @("Q", "R", "S", "T", "U", "W", "X", "Y", "Z")) {
        $drive = "${letter}:"
        if (Test-Path -LiteralPath "${drive}\") {
            continue
        }
        & $subst.Source $drive $Root
        if ($LASTEXITCODE -eq 0) {
            return [PSCustomObject]@{ Root = "${drive}\"; Drive = $drive }
        }
    }
    throw "No free drive letter is available for the Fairy desktop path alias"
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$profilesPath = Join-Path $repositoryRoot "config\openrouter.providers.json"

$profiles = Get-Content -LiteralPath $profilesPath -Raw
$null = $profiles | ConvertFrom-Json -ErrorAction Stop
$key = $null
if ($OpenRouterKeyFile) {
    $resolvedKeyFile = Resolve-Path -LiteralPath $OpenRouterKeyFile -ErrorAction Stop
    if (-not (Test-Path -LiteralPath $resolvedKeyFile -PathType Leaf)) {
        throw "OpenRouter key file is not a regular file"
    }
    $key = (Get-Content -LiteralPath $resolvedKeyFile -Raw).Trim()
    if ($key.Length -lt 20) {
        throw "OpenRouter key file is empty or malformed"
    }
}

$environmentNames = @(
    "FAIRY_PROVIDER_PROFILES_JSON",
    "FAIRY_PROVIDER_SECRET_REFS_JSON",
    "FAIRY_PROVIDER_SECRET_OPENROUTER"
)
$previousEnvironment = @{}
$pathAlias = $null
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable(
        $name,
        [EnvironmentVariableTarget]::Process
    )
}

try {
    $env:FAIRY_PROVIDER_PROFILES_JSON = $profiles
    if ($null -ne $key) {
        $env:FAIRY_PROVIDER_SECRET_REFS_JSON = '{"openrouter":"FAIRY_PROVIDER_SECRET_OPENROUTER"}'
        $env:FAIRY_PROVIDER_SECRET_OPENROUTER = $key
    }
    else {
        Remove-Item Env:FAIRY_PROVIDER_SECRET_REFS_JSON -ErrorAction SilentlyContinue
        Remove-Item Env:FAIRY_PROVIDER_SECRET_OPENROUTER -ErrorAction SilentlyContinue
    }

    $pathAlias = New-DesktopPathAlias -Root $repositoryRoot
    $desktopPath = Join-Path $pathAlias.Root "desktop"
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
    if ($null -ne $pathAlias -and $null -ne $pathAlias.Drive) {
        & subst.exe $pathAlias.Drive /D
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Could not remove desktop workspace alias $($pathAlias.Drive)"
        }
    }
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $previousEnvironment[$name],
            [EnvironmentVariableTarget]::Process
        )
    }
    $key = $null
}
