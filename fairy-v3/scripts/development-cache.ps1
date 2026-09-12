[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param([switch]$Clean, [string[]]$Only = @())

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
# Deliberately exclude models, runtimes, virtualenvs, databases, downloads and backups.
$Candidates = @(
    'desktop/src-tauri/target', 'desktop/test-results', 'desktop/playwright-report',
    'desktop/node_modules/.vite', 'core/.pytest_cache', 'core/.ruff_cache',
    'cloud/.pytest_cache', 'cloud/.ruff_cache', 'capabilities/.pytest_cache',
    'voice-worker/.pytest_cache'
)
foreach ($Name in $Only) {
    if ($Candidates -notcontains $Name) { throw "Not an allowlisted build cache: $Name" }
}
if ($Clean) {
    # Refuse deletion while any potentially related developer process cannot be ruled out.
    $Active = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^(node|python[0-9.]*|cargo|rustc|fairy.*|stdio_worker)(\.exe)?$' -and
        ([string]::IsNullOrEmpty($_.CommandLine) -or
         $_.CommandLine.IndexOf($ProjectRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
         $_.CommandLine -match '(?i)fairy[-_](core|voice|realtime|local|desktop)|vite|vitest|playwright')
    })
    if ($Active.Count -ne 0) {
        throw "Cache cleanup refused: $($Active.Count) active or unidentifiable developer processes. Close them first."
    }
}
foreach ($Name in $Candidates) {
    if ($Only.Count -ne 0 -and $Only -notcontains $Name) { continue }
    $Target = [IO.Path]::GetFullPath((Join-Path $ProjectRoot $Name))
    if (-not $Target.StartsWith($ProjectRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) { throw 'Cache path escaped project' }
    if (-not (Test-Path -LiteralPath $Target)) { continue }
    # Check every ancestor and child; never follow junctions/symlinks during inventory or deletion.
    $Ancestor = Get-Item -LiteralPath $Target -Force
    while ($Ancestor.FullName.Length -gt $ProjectRoot.Length) {
        if (($Ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Reparse point blocks cache access: $Name"
        }
        $Ancestor = $Ancestor.Parent
    }
    $Pending = [Collections.Generic.Stack[string]]::new()
    $Pending.Push($Target)
    [long]$Bytes = 0
    [long]$Files = 0
    while ($Pending.Count -gt 0) {
        foreach ($Item in (Get-ChildItem -LiteralPath $Pending.Pop() -Force)) {
            if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Reparse point inside cache blocks cleanup: $Name"
            }
            if ($Item.PSIsContainer) { $Pending.Push($Item.FullName) }
            else { $Files++; $Bytes += $Item.Length }
        }
    }
    [PSCustomObject]@{ Cache = $Name; Files = $Files; GiB = [math]::Round($Bytes / 1GB, 3); Bytes = $Bytes }
    if ($Clean -and $PSCmdlet.ShouldProcess($Target, 'Delete reproducible build cache (not recoverable from recycle bin)')) {
        Remove-Item -LiteralPath $Target -Recurse -Force
        Write-Host "Removed $Name; recreate using the normal development tools."
    }
}
