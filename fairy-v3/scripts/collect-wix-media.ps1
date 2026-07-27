[CmdletBinding()]
param(
    [string]$Architecture = "x64"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
$tauriRoot = Join-Path $root "desktop\src-tauri"
$configuration = Get-Content -Raw -LiteralPath (Join-Path $tauriRoot "tauri.conf.json") | ConvertFrom-Json
$version = [string]$configuration.version
$source = [System.IO.Path]::GetFullPath((Join-Path $tauriRoot "target\release\wix\$Architecture"))
$destination = [System.IO.Path]::GetFullPath((Join-Path $tauriRoot "target\release\bundle\msi"))

foreach ($path in @($source, $destination)) {
    if (-not $path.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "WiX media path escaped the repository: $path"
    }
}
if (-not (Test-Path -LiteralPath $source -PathType Container)) {
    throw "WiX working directory is missing: $source"
}
New-Item -ItemType Directory -Force -Path $destination | Out-Null

$msiName = "Fairy_${version}_${Architecture}_en-US.msi"
$msiPath = Join-Path $destination $msiName
if (-not (Test-Path -LiteralPath $msiPath -PathType Leaf)) {
    throw "Tauri MSI output is missing: $msiPath"
}

function Get-ExternalCabinetNames([string]$InstallerPath) {
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $database = $installer.GetType().InvokeMember(
        "OpenDatabase", "InvokeMethod", $null, $installer, @($InstallerPath, 0)
    )
    $view = $database.GetType().InvokeMember(
        "OpenView", "InvokeMethod", $null, $database,
        @('SELECT `Cabinet` FROM `Media` ORDER BY `DiskId`')
    )
    $view.GetType().InvokeMember("Execute", "InvokeMethod", $null, $view, $null) | Out-Null
    $names = [System.Collections.Generic.List[string]]::new()
    while ($true) {
        $record = $view.GetType().InvokeMember("Fetch", "InvokeMethod", $null, $view, $null)
        if ($null -eq $record) { break }
        $name = [string]$record.GetType().InvokeMember("StringData", "GetProperty", $null, $record, 1)
        if ([string]::IsNullOrWhiteSpace($name) -or $name.StartsWith("#")) { continue }
        if ([System.IO.Path]::GetFileName($name) -ne $name -or -not $name.EndsWith(".cab", [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "MSI references an unsafe external cabinet name: $name"
        }
        $names.Add($name)
    }
    return $names
}

$cabinetNames = @(Get-ExternalCabinetNames $msiPath)
if ($cabinetNames.Count -eq 0) {
    throw "WiX did not produce external cabinet media"
}
if (@($cabinetNames | Select-Object -Unique).Count -ne $cabinetNames.Count) {
    throw "WiX references duplicate external cabinet media"
}
$cabinets = @(
    foreach ($cabinetName in $cabinetNames) {
        $cabinetPath = Join-Path $source $cabinetName
        if (-not (Test-Path -LiteralPath $cabinetPath -PathType Leaf)) {
            throw "MSI external cabinet is missing from the WiX directory: $cabinetName"
        }
        Get-Item -LiteralPath $cabinetPath
    }
)
foreach ($stale in @(Get-ChildItem -LiteralPath $destination -File -Filter "*.cab" -ErrorAction SilentlyContinue)) {
    Remove-Item -LiteralPath $stale.FullName -Force
}
foreach ($cabinet in $cabinets) {
    if ($cabinet.Length -ge 2GB) {
        throw "WiX cabinet exceeds the Windows Installer media limit: $($cabinet.Name)"
    }
    Copy-Item -LiteralPath $cabinet.FullName -Destination (Join-Path $destination $cabinet.Name) -Force
}

$releaseFiles = @((Get-Item -LiteralPath $msiPath)) + @(
    foreach ($cabinetName in ($cabinetNames | Sort-Object)) {
        Get-Item -LiteralPath (Join-Path $destination $cabinetName)
    }
)
$manifestFiles = @(
    foreach ($file in $releaseFiles) {
        [ordered]@{
            name = $file.Name
            bytes = $file.Length
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
        }
    }
)
$totalBytes = ($releaseFiles | Measure-Object -Property Length -Sum).Sum
$manifest = [ordered]@{
    schema_version = 2
    product = "Fairy"
    version = $version
    architecture = $Architecture
    installer = $msiName
    cabinets = @($cabinetNames | Sort-Object)
    artifact_count = $manifestFiles.Count
    media = $manifestFiles
    total_bytes = $totalBytes
}
$manifestPath = Join-Path $destination "release-manifest.json"
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8

[pscustomobject]@{
    manifest = $manifestPath
    media_files = $manifestFiles.Count
    total_gib = [math]::Round($manifest.total_bytes / 1GB, 2)
    installer_sha256 = $manifestFiles[0].sha256
} | ConvertTo-Json
