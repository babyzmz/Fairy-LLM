[CmdletBinding()]
param(
    [ValidateSet("contract", "upstream-cpu", "production-cuda")]
    [string]$Profile = "contract",
    [switch]$Test
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Some managed launchers expose both `Path` and `PATH` on Windows. MSBuild
# materializes the inherited environment into a case-insensitive dictionary and
# fails before invoking CL when both spellings are present.
$processPath = [Environment]::GetEnvironmentVariable("Path", "Process")
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")
[Environment]::SetEnvironmentVariable("Path", $processPath, "Process")

if ($Profile -ne "contract") {
    throw "Build profile '$Profile' is not implemented until the upstream adapter task."
}

$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$bootstrapScript = Join-Path $PSScriptRoot "bootstrap-cmake.ps1"
$syncScript = Join-Path $PSScriptRoot "sync-upstream.ps1"

& $syncScript -Offline | Out-Null
$cmake = (& $bootstrapScript -Offline | Select-Object -Last 1).Trim()
if (-not (Test-Path -LiteralPath $cmake -PathType Leaf)) {
    throw "Pinned CMake executable is unavailable."
}

& $cmake --fresh --preset $Profile -S $runtimeRoot
if ($LASTEXITCODE -ne 0) {
    throw "CMake configure failed for '$Profile'."
}
$buildRoot = Join-Path $runtimeRoot "out\$Profile"
& $cmake --build $buildRoot --config Release
if ($LASTEXITCODE -ne 0) {
    throw "CMake build failed for '$Profile'."
}
if ($Test) {
    & $cmake --build $buildRoot --target RUN_TESTS --config Release
    if ($LASTEXITCODE -ne 0) {
        throw "CMake tests failed for '$Profile'."
    }
}

$executable = Join-Path $runtimeRoot "out\$Profile\Release\fairy-omni-runtime.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Expected runtime executable '$executable' is missing."
}
if ($Test) {
    & (Join-Path $PSScriptRoot "control-integration.tests.ps1") -Executable $executable
}
Write-Output $executable
