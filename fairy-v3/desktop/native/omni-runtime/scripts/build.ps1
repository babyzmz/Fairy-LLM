[CmdletBinding()]
param(
    [ValidateSet("contract", "upstream-cpu", "production-cuda")]
    [string]$Profile = "contract",
    [string]$CudaToolchainRoot,
    [switch]$OfflineToolchain,
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

$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$bootstrapScript = Join-Path $PSScriptRoot "bootstrap-cmake.ps1"
$syncScript = Join-Path $PSScriptRoot "sync-upstream.ps1"
$cudaToolchainScript = Join-Path $PSScriptRoot "cuda-toolchain.ps1"

function Import-VisualStudioEnvironment {
    $vswhereCandidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"),
        (Join-Path $env:ProgramFiles "Microsoft Visual Studio\Installer\vswhere.exe")
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    $vswhere = $vswhereCandidates | Where-Object {
        Test-Path -LiteralPath $_ -PathType Leaf
    } | Select-Object -First 1

    $installationPath = $null
    if ($null -ne $vswhere) {
        $installationPath = @(
            & $vswhere `
                -latest `
                -products * `
                -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
                -property installationPath
        ) | Select-Object -Last 1
    }
    if ([string]::IsNullOrWhiteSpace([string]$installationPath)) {
        $standardRoots = @(
            (Join-Path $env:ProgramFiles "Microsoft Visual Studio\2022\BuildTools"),
            (Join-Path $env:ProgramFiles "Microsoft Visual Studio\2022\Community"),
            (Join-Path $env:ProgramFiles "Microsoft Visual Studio\2022\Professional"),
            (Join-Path $env:ProgramFiles "Microsoft Visual Studio\2022\Enterprise")
        )
        $installationPath = $standardRoots | Where-Object {
            Test-Path -LiteralPath (Join-Path $_ "Common7\Tools\VsDevCmd.bat") -PathType Leaf
        } | Select-Object -First 1
    }
    if ([string]::IsNullOrWhiteSpace([string]$installationPath)) {
        throw "Visual Studio 2022 with the x64 C++ toolchain is required."
    }

    $vsDevCmd = Join-Path ([string]$installationPath) "Common7\Tools\VsDevCmd.bat"
    if (-not (Test-Path -LiteralPath $vsDevCmd -PathType Leaf)) {
        throw "Visual Studio's VsDevCmd.bat is missing."
    }
    $environmentLines = @(
        & $env:ComSpec /d /s /c "`"$vsDevCmd`" -no_logo -arch=x64 -host_arch=x64 >nul && set"
    )
    if ($LASTEXITCODE -ne 0) {
        throw "The Visual Studio x64 environment could not be initialized."
    }
    foreach ($line in $environmentLines) {
        if ($line -notmatch '^([^=][^=]*)=(.*)$') {
            continue
        }
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
    }
    if ($null -eq (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
        throw "The Visual Studio x64 compiler is unavailable after initialization."
    }
}

$cmakeArguments = @()
if ($Profile -eq "contract") {
    & $syncScript -Offline | Out-Null
} else {
    $verifyResult = & (Join-Path $PSScriptRoot "verify-patches.ps1") | Select-Object -Last 1 |
        ConvertFrom-Json
    $patchedSource = [IO.Path]::GetFullPath([string]$verifyResult.patched_source_root)
    $cmakeArguments += "-DFAIRY_OMNI_PATCHED_SOURCE=$patchedSource"
}
if ($Profile -eq "production-cuda") {
    Import-VisualStudioEnvironment
    $toolchainArguments = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $cudaToolchainScript
    )
    if (-not [string]::IsNullOrWhiteSpace($CudaToolchainRoot)) {
        $toolchainArguments += @("-ToolchainRoot", $CudaToolchainRoot)
    }
    if ($OfflineToolchain) {
        $toolchainArguments += "-Offline"
    }
    $toolchainOutput = @(& powershell @toolchainArguments)
    if ($LASTEXITCODE -ne 0 -or $toolchainOutput.Count -eq 0) {
        throw "The pinned CUDA toolchain is unavailable."
    }
    $cudaToolchain = [string]$toolchainOutput[-1] | ConvertFrom-Json
    $env:Path = "$([string]$cudaToolchain.bin);$env:Path"
    $env:CUDAToolkit_ROOT = [string]$cudaToolchain.cmake_root
    $cmakeArguments += "-DCMAKE_CUDA_COMPILER=$([string]$cudaToolchain.nvcc)"
    $cmakeArguments += "-DCMAKE_MAKE_PROGRAM=$([string]$cudaToolchain.ninja)"
}
$cmakeOutput = if ($Profile -eq "production-cuda" -and -not $OfflineToolchain) {
    & $bootstrapScript
} else {
    & $bootstrapScript -Offline
}
$cmake = ([string]@($cmakeOutput)[-1]).Trim()
if (-not (Test-Path -LiteralPath $cmake -PathType Leaf)) {
    throw "Pinned CMake executable is unavailable."
}

& $cmake --fresh --preset $Profile -S $runtimeRoot @cmakeArguments
if ($LASTEXITCODE -ne 0) {
    throw "CMake configure failed for '$Profile'."
}
$buildRoot = Join-Path $runtimeRoot "out\$Profile"
& $cmake --build $buildRoot --config Release
if ($LASTEXITCODE -ne 0) {
    throw "CMake build failed for '$Profile'."
}
if ($Test -and $Profile -eq "contract") {
    & $cmake --build $buildRoot --target RUN_TESTS --config Release
    if ($LASTEXITCODE -ne 0) {
        throw "CMake tests failed for '$Profile'."
    }
}

$executable = Join-Path $runtimeRoot "out\$Profile\Release\fairy-omni-runtime.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Expected runtime executable '$executable' is missing."
}
if ($Test -and $Profile -eq "contract") {
    & (Join-Path $PSScriptRoot "control-integration.tests.ps1") -Executable $executable
}
if ($Profile -ne "contract") {
    & (Join-Path $PSScriptRoot "backend-boundary.tests.ps1") -PatchedSource $patchedSource
}
Write-Output $executable
