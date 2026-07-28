[CmdletBinding()]
param(
    [string]$BaselineInstaller,
    [string]$CandidateInstaller,
    [string]$CandidateExecutable,
    [switch]$ValidateMediaOnly,
    [switch]$KeepInstalled
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($CandidateInstaller)) {
    $CandidateInstaller = Join-Path $root "desktop\src-tauri\target\release\bundle\msi\Fairy_0.2.0_x64_en-US.msi"
}
if ([string]::IsNullOrWhiteSpace($CandidateExecutable)) {
    $CandidateExecutable = Join-Path $root "desktop\src-tauri\target\release\fairy.exe"
}

if ($env:OS -ne "Windows_NT") {
    throw "PRESENCE_PLATFORM_UNSUPPORTED: Windows is required"
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isElevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$installScope = if ($isElevated) { "per-machine" } else { "per-user" }
$msiScopeProperties = if ($isElevated) { "" } else { " ALLUSERS=2 MSIINSTALLPERUSER=1" }
$requiredPaths = if ($ValidateMediaOnly) {
    @($CandidateInstaller)
} else {
    @($BaselineInstaller, $CandidateInstaller, $CandidateExecutable)
}
foreach ($path in $requiredPaths) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Release installation dependency is missing: $path"
    }
}

function Assert-ReleaseMedia([string]$Installer) {
    if ([System.IO.Path]::GetExtension($Installer) -ine ".msi") { return }
    $directory = Split-Path -Parent (Resolve-Path -LiteralPath $Installer).Path
    $manifestPath = Join-Path $directory "release-manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Split MSI release manifest is missing: $manifestPath"
    }
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    if (
        [int]$manifest.schema_version -ne 2 -or
        [string]$manifest.product -ne "Fairy" -or
        [string]$manifest.architecture -ne "x64"
    ) {
        throw "Split MSI release manifest schema or product is unsupported"
    }
    if ($manifest.installer -ne (Split-Path -Leaf $Installer)) {
        throw "Split MSI manifest does not identify the candidate installer"
    }
    $mediaEntries = @($manifest.media)
    $cabinetNames = @($manifest.cabinets | ForEach-Object { [string]$_ })
    if (
        $mediaEntries.Count -ne [int]$manifest.artifact_count -or
        $cabinetNames.Count -eq 0 -or
        @($cabinetNames | Select-Object -Unique).Count -ne $cabinetNames.Count
    ) {
        throw "Split MSI manifest media count or cabinet set is invalid"
    }
    $declaredMediaNames = @($mediaEntries | ForEach-Object { [string]$_.name })
    $expectedMediaNames = @([string]$manifest.installer) + $cabinetNames
    if (
        $declaredMediaNames.Count -ne $expectedMediaNames.Count -or
        @(Compare-Object $declaredMediaNames $expectedMediaNames).Count -ne 0
    ) {
        throw "Split MSI manifest media does not match its installer and cabinets"
    }
    $seenMedia = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    [long]$totalBytes = 0
    foreach ($media in $mediaEntries) {
        $mediaName = [string]$media.name
        if ([System.IO.Path]::GetFileName($mediaName) -ne $mediaName -or -not $seenMedia.Add($mediaName)) {
            throw "Split MSI manifest contains an unsafe or duplicate media name: $mediaName"
        }
        $mediaPath = Join-Path $directory $mediaName
        if (-not (Test-Path -LiteralPath $mediaPath -PathType Leaf)) {
            throw "Split MSI media is missing: $mediaName"
        }
        $mediaFile = Get-Item -LiteralPath $mediaPath
        if ($mediaFile.Length -ne [long]$media.bytes) {
            throw "Split MSI media size mismatch: $mediaName"
        }
        $totalBytes += $mediaFile.Length
        $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $mediaPath).Hash
        if ($actualHash -ine [string]$media.sha256) {
            throw "Split MSI media hash mismatch: $mediaName"
        }
    }
    if ($totalBytes -ne [long]$manifest.total_bytes) {
        throw "Split MSI manifest total size does not match its media"
    }
}

Assert-ReleaseMedia $CandidateInstaller
if ($ValidateMediaOnly) {
    [pscustomobject]@{
        schema_version = 1
        installer = (Resolve-Path -LiteralPath $CandidateInstaller).Path
        release_media_valid = $true
    } | ConvertTo-Json -Compress
    return
}

function Get-FairyUninstallEntry {
    $registryPaths = @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    return $registryPaths |
        ForEach-Object { Get-ItemProperty -Path $_ -ErrorAction SilentlyContinue } |
        Where-Object {
            $property = $_.PSObject.Properties["DisplayName"]
            $null -ne $property -and [string]$property.Value -eq "Fairy"
        } |
        Select-Object -First 1
}

function Wait-FairyUninstallEntry([bool]$Present, [int]$TimeoutSeconds = 30) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $entry = Get-FairyUninstallEntry
        if ($Present -and $null -ne $entry) { return $entry }
        if (-not $Present -and $null -eq $entry) { return $null }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Fairy uninstall registration did not reach present=$Present"
}

function Invoke-SilentInstaller([string]$Path) {
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if ([System.IO.Path]::GetExtension($resolved) -ieq ".msi") {
        $arguments = "/i `"$resolved`"$msiScopeProperties /qn /norestart"
        $process = Start-Process -FilePath "msiexec.exe" -ArgumentList $arguments -PassThru -Wait -WindowStyle Hidden
    } else {
        $process = Start-Process -FilePath $resolved -ArgumentList "/S" -PassThru -Wait -WindowStyle Hidden
    }
    if ($process.ExitCode -ne 0) {
        throw "Installer exited with code $($process.ExitCode): $Path"
    }
}

function Split-RegisteredCommand([string]$Command) {
    if ($Command -match '^\s*"([^"]+)"\s*(.*)$') {
        return @($matches[1], $matches[2])
    }
    if ($Command -match '^\s*(\S+)\s*(.*)$') {
        return @($matches[1], $matches[2])
    }
    throw "Unable to parse registered command: $Command"
}

function Get-EntryValue($Entry, [string]$Name) {
    $property = $Entry.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) { return "" }
    return [string]$property.Value
}

function Get-InstalledFairyExecutable($Entry) {
    $candidates = [System.Collections.Generic.List[string]]::new()
    $installLocation = Get-EntryValue $Entry "InstallLocation"
    $displayIcon = Get-EntryValue $Entry "DisplayIcon"
    $uninstallString = Get-EntryValue $Entry "UninstallString"
    if (-not [string]::IsNullOrWhiteSpace($installLocation)) {
        $candidates.Add((Join-Path $installLocation "fairy.exe"))
    }
    if (-not [string]::IsNullOrWhiteSpace($displayIcon)) {
        $iconPath = $displayIcon.Trim('"').Split(',')[0]
        $candidates.Add($iconPath)
    }
    if (-not [string]::IsNullOrWhiteSpace($uninstallString)) {
        $uninstallParts = Split-RegisteredCommand $uninstallString
        $uninstallDirectory = Split-Path -Parent $uninstallParts[0]
        if (-not [string]::IsNullOrWhiteSpace($uninstallDirectory)) {
            $candidates.Add((Join-Path $uninstallDirectory "fairy.exe"))
        }
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Installed Fairy executable could not be resolved from the uninstall registration"
}

function Uninstall-Fairy {
    $entry = Get-FairyUninstallEntry
    if ($null -eq $entry) { return }
    if ((Get-EntryValue $entry "WindowsInstaller") -eq "1") {
        $productCode = Get-EntryValue $entry "PSChildName"
        $process = Start-Process -FilePath "msiexec.exe" -ArgumentList "/x $productCode /qn /norestart" -PassThru -Wait -WindowStyle Hidden
        if ($process.ExitCode -ne 0) {
            throw "Fairy MSI uninstaller exited with code $($process.ExitCode)"
        }
        Wait-FairyUninstallEntry $false | Out-Null
        return
    }
    $quietUninstallString = Get-EntryValue $entry "QuietUninstallString"
    $uninstallString = Get-EntryValue $entry "UninstallString"
    $registeredCommand = if (-not [string]::IsNullOrWhiteSpace($quietUninstallString)) {
        $quietUninstallString
    } else {
        $uninstallString
    }
    $parts = Split-RegisteredCommand $registeredCommand
    $arguments = $parts[1]
    if ($arguments -notmatch '(^|\s)/S($|\s)') {
        $arguments = ($arguments + " /S").Trim()
    }
    $process = Start-Process -FilePath $parts[0] -ArgumentList $arguments -PassThru -Wait -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "Fairy uninstaller exited with code $($process.ExitCode)"
    }
    Wait-FairyUninstallEntry $false | Out-Null
}

if ($null -ne (Get-FairyUninstallEntry)) {
    throw "A Fairy installation already exists; refusing to replace a user installation during the release test"
}
$knownInstallDirectories = [System.Collections.Generic.List[string]]::new()
$knownInstallDirectories.Add((Join-Path $env:LOCALAPPDATA "Programs\Fairy"))
foreach ($programFilesRoot in @($env:ProgramW6432, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    if (-not [string]::IsNullOrWhiteSpace($programFilesRoot)) {
        $knownInstallDirectories.Add((Join-Path $programFilesRoot "Fairy"))
    }
}
$knownInstallDirectories.Add((Join-Path ([Environment]::GetFolderPath("Desktop")) "Fairy.lnk"))
$knownInstallDirectories.Add((Join-Path ([Environment]::GetFolderPath("Programs")) "Fairy"))
$orphanedInstallDirectories = @(
    $knownInstallDirectories |
        Select-Object -Unique |
        Where-Object { Test-Path -LiteralPath $_ }
)
if ($orphanedInstallDirectories.Count -gt 0) {
    throw "A Fairy installation directory exists without registration; refusing to overwrite it: $($orphanedInstallDirectories -join ', ')"
}

$baselineExecutable = $null
$candidateInstalledExecutable = $null
$baselineHash = $null
$candidateHash = $null
$sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $CandidateExecutable).Hash
$sourceVersionInfo = (Get-Item -LiteralPath $CandidateExecutable).VersionInfo
$sourceVersion = [string]$sourceVersionInfo.ProductVersion
try {
    Invoke-SilentInstaller $BaselineInstaller
    $baselineEntry = Wait-FairyUninstallEntry $true
    $baselineExecutable = Get-InstalledFairyExecutable $baselineEntry
    $baselineHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $baselineExecutable).Hash

    Invoke-SilentInstaller $CandidateInstaller
    $candidateEntry = Wait-FairyUninstallEntry $true
    $candidateInstalledExecutable = Get-InstalledFairyExecutable $candidateEntry
    $candidateHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $candidateInstalledExecutable).Hash
    if ($candidateHash -eq $baselineHash) {
        throw "Candidate installation did not replace the baseline executable"
    }
    $candidateRegisteredVersion = Get-EntryValue $candidateEntry "DisplayVersion"
    $candidateVersionInfo = (Get-Item -LiteralPath $candidateInstalledExecutable).VersionInfo
    if ($candidateRegisteredVersion -ne $sourceVersion) {
        throw "Installed candidate registration version does not match $sourceVersion"
    }
    if (
        [string]$candidateVersionInfo.ProductName -ne "Fairy" -or
        [string]$candidateVersionInfo.FileVersion -ne $sourceVersion -or
        [string]$candidateVersionInfo.ProductVersion -ne $sourceVersion
    ) {
        throw "Installed candidate PE metadata does not match Fairy $sourceVersion"
    }

    $nativeProbe = Join-Path $root "scripts\test-presence-native.ps1"
    $probeOutput = & powershell -NoProfile -ExecutionPolicy Bypass -File $nativeProbe -Executable $candidateInstalledExecutable -ExpectedMode liquid
    if ($LASTEXITCODE -ne 0) {
        throw "Installed candidate failed the native Presence smoke test"
    }

    [pscustomobject]@{
        install_scope = $installScope
        baseline_executable = $baselineExecutable
        baseline_hash = $baselineHash.ToLowerInvariant()
        candidate_executable = $candidateInstalledExecutable
        candidate_hash = $candidateHash.ToLowerInvariant()
        candidate_source_hash = $sourceHash.ToLowerInvariant()
        candidate_version = $candidateRegisteredVersion
        native_probe = (($probeOutput -join "`n") | ConvertFrom-Json)
        upgrade_replaced_binary = $true
    } | ConvertTo-Json -Depth 6
}
finally {
    if (-not $KeepInstalled) {
        Uninstall-Fairy
        $installedExecutable = if ($null -ne $candidateInstalledExecutable) {
            $candidateInstalledExecutable
        } else {
            $baselineExecutable
        }
        if ($null -ne $installedExecutable) {
            $installedDirectory = Split-Path -Parent $installedExecutable
            $residualPaths = @(@(
                $installedDirectory,
                (Join-Path ([Environment]::GetFolderPath("Desktop")) "Fairy.lnk"),
                (Join-Path ([Environment]::GetFolderPath("Programs")) "Fairy")
            ) | Where-Object { Test-Path -LiteralPath $_ })
            if ($residualPaths.Count -gt 0) {
                throw "Fairy uninstall left installation artifacts behind: $($residualPaths -join ', ')"
            }
        }
    }
}
