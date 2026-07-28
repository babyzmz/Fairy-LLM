[CmdletBinding()]
param(
    [string]$ToolchainRoot,
    [string]$LockPath,
    [switch]$Offline,
    [switch]$ValidateLockOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-AsciiPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($Path -cnotmatch '^[\x00-\x7F]+$') {
        throw "The CUDA toolchain root must use an ASCII-safe path."
    }
}

function Assert-Sha256 {
    param(
        [Parameter(Mandatory = $true)][string]$Value,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if ($Value -cnotmatch '^[0-9a-f]{64}$') {
        throw "$Label must be a lowercase SHA-256 digest."
    }
}

function Assert-SafeCacheNamespace {
    param([Parameter(Mandatory = $true)][string]$Value)

    if ($Value -cnotmatch '^[a-z0-9][a-z0-9._-]{0,127}$') {
        throw "The CUDA cache namespace is unsafe."
    }
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-Lock {
    param([Parameter(Mandatory = $true)]$Lock)

    if ([int]$Lock.schema_version -ne 1 -or [string]$Lock.platform -ne "win-64") {
        throw "The CUDA toolchain lock schema or platform is unsupported."
    }
    Assert-SafeCacheNamespace -Value ([string]$Lock.cache_namespace)

    $micromamba = $Lock.micromamba
    if ([string]$micromamba.version -ne "2.4.0") {
        throw "The CUDA toolchain requires micromamba 2.4.0."
    }
    if (
        [string]$micromamba.url -ne
        "https://github.com/mamba-org/micromamba-releases/releases/download/2.4.0-0/micromamba-win-64"
    ) {
        throw "The micromamba origin is not approved."
    }
    if ([string]$micromamba.archive -cnotmatch '^[a-zA-Z0-9._-]+$') {
        throw "The micromamba archive name is unsafe."
    }
    Assert-Sha256 -Value ([string]$micromamba.sha256) -Label "micromamba archive"
    Assert-Sha256 -Value ([string]$micromamba.executable_sha256) -Label "micromamba executable"

    $requirements = $Lock.requirements
    if (
        [string]$requirements.cuda_compiler -ne "13.0.3" -or
        [string]$requirements.nvcc -ne "13.0.88" -or
        [string]$requirements.ninja -ne "1.13.2" -or
        [string]$requirements.libcublas -ne "13.1.1.3" -or
        [string]$requirements.architecture -ne "x64" -or
        [string]$requirements.license -ne "LicenseRef-NVIDIA-End-User-License-Agreement"
    ) {
        throw "The CUDA toolchain requirements do not match the reviewed production baseline."
    }

    $requiredFiles = @($Lock.required_files)
    $expectedFiles = @(
        "Library/bin/nvcc.exe",
        "Library/bin/ninja.exe",
        "Library/include/cuda.h",
        "Library/lib/x64/cublas.lib",
        "Library/bin/cublas64_13.dll",
        "Library/bin/cublasLt64_13.dll"
    )
    if (
        $requiredFiles.Count -ne $expectedFiles.Count -or
        @(Compare-Object $requiredFiles $expectedFiles).Count -ne 0
    ) {
        throw "The CUDA toolchain required-file set is not exact."
    }

    $components = @($Lock.runtime_components)
    if ($components.Count -ne 2) {
        throw "The CUDA runtime component set is not exact."
    }
    $componentNames = @($components | ForEach-Object { [string]$_.name })
    if (
        @(Compare-Object $componentNames @("cublas64_13.dll", "cublasLt64_13.dll")).Count -ne 0
    ) {
        throw "The CUDA runtime component names are not exact."
    }
    foreach ($component in $components) {
        if (
            [string]$component.package -ne "libcublas" -or
            [string]$component.version -ne "13.1.1.3" -or
            [string]$component.license -ne "LicenseRef-NVIDIA-End-User-License-Agreement" -or
            [int64]$component.bytes -le 0
        ) {
            throw "The CUDA runtime component metadata is invalid."
        }
        Assert-Sha256 -Value ([string]$component.sha256) -Label ([string]$component.name)
    }

    $packages = @($Lock.packages)
    if ($packages.Count -ne 37) {
        throw "The CUDA explicit package set must contain exactly 37 packages."
    }
    $seenNames = @{}
    foreach ($package in $packages) {
        $name = [string]$package.name
        if ($name -cnotmatch '^[a-z0-9][a-z0-9._-]*$' -or $seenNames.ContainsKey($name)) {
            throw "The CUDA package set contains an unsafe or duplicate name."
        }
        $seenNames[$name] = $true
        $url = [string]$package.url
        if (
            -not (
                $url.StartsWith("https://conda.anaconda.org/conda-forge/", [StringComparison]::Ordinal) -or
                $url.StartsWith(
                    "https://conda.anaconda.org/nvidia/label/cuda-13.0.3/",
                    [StringComparison]::Ordinal
                )
            ) -or
            $url -cnotmatch '\.conda$'
        ) {
            throw "The CUDA package '$name' has an unapproved origin."
        }
        if (
            [string]::IsNullOrWhiteSpace([string]$package.version) -or
            [string]::IsNullOrWhiteSpace([string]$package.build) -or
            [string]::IsNullOrWhiteSpace([string]$package.license)
        ) {
            throw "The CUDA package '$name' has incomplete identity metadata."
        }
        Assert-Sha256 -Value ([string]$package.sha256) -Label "CUDA package '$name'"
    }

    foreach ($pin in @{
        "cuda-compiler" = "13.0.3"
        "cuda-nvcc" = "13.0.88"
        "libcublas" = "13.1.1.3"
        "libcublas-dev" = "13.1.1.3"
        "ninja" = "1.13.2"
    }.GetEnumerator()) {
        $matched = @($packages | Where-Object {
            [string]$_.name -eq $pin.Key -and [string]$_.version -eq $pin.Value
        })
        if ($matched.Count -ne 1) {
            throw "The CUDA package pin '$($pin.Key)=$($pin.Value)' is missing."
        }
    }
}

function Assert-ToolchainEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$EnvironmentRoot,
        [Parameter(Mandatory = $true)]$Lock
    )

    if (-not (Test-Path -LiteralPath $EnvironmentRoot -PathType Container)) {
        throw "The CUDA toolchain environment is missing."
    }
    foreach ($relative in @($Lock.required_files)) {
        $candidate = Join-Path $EnvironmentRoot ([string]$relative).Replace("/", "\")
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "The CUDA toolchain is incomplete: '$relative' is missing."
        }
    }

    $metadataRoot = Join-Path $EnvironmentRoot "conda-meta"
    if (-not (Test-Path -LiteralPath $metadataRoot -PathType Container)) {
        throw "The CUDA toolchain package metadata is missing."
    }
    $installed = @{}
    foreach ($metadataPath in @(Get-ChildItem -LiteralPath $metadataRoot -Filter "*.json" -File)) {
        $metadata = Get-Content -LiteralPath $metadataPath.FullName -Raw -Encoding UTF8 |
            ConvertFrom-Json
        $name = [string]$metadata.name
        if ($installed.ContainsKey($name)) {
            throw "The CUDA toolchain contains duplicate package metadata."
        }
        $installed[$name] = $metadata
    }
    $packages = @($Lock.packages)
    if ($installed.Count -ne $packages.Count) {
        throw "The CUDA toolchain package set does not match the lock."
    }
    foreach ($package in $packages) {
        $name = [string]$package.name
        if (-not $installed.ContainsKey($name)) {
            throw "The CUDA toolchain package '$name' is missing."
        }
        $metadata = $installed[$name]
        if (
            [string]$metadata.version -ne [string]$package.version -or
            [string]$metadata.build -ne [string]$package.build
        ) {
            throw "The CUDA toolchain package '$name' does not match the lock."
        }
        $licenseProperty = $metadata.PSObject.Properties["license"]
        if (
            $null -ne $licenseProperty -and
            -not [string]::IsNullOrWhiteSpace([string]$licenseProperty.Value) -and
            [string]$licenseProperty.Value -ne [string]$package.license
        ) {
            throw "The CUDA toolchain package '$name' license does not match the lock."
        }
    }

    foreach ($component in @($Lock.runtime_components)) {
        $path = Join-Path $EnvironmentRoot "Library\bin\$([string]$component.name)"
        $item = Get-Item -LiteralPath $path
        if (
            [int64]$item.Length -ne [int64]$component.bytes -or
            (Get-FileSha256 -Path $path) -ne [string]$component.sha256
        ) {
            throw "The CUDA runtime component '$([string]$component.name)' failed integrity validation."
        }
    }
}

function Save-VerifiedDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Sha256,
        [switch]$Offline
    )

    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        if ((Get-FileSha256 -Path $Destination) -eq $Sha256) {
            return
        }
        throw "A cached toolchain download failed integrity validation."
    }
    if ($Offline) {
        throw "The CUDA toolchain cache is incomplete and offline mode was requested."
    }

    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $partial = "$Destination.partial-$([Guid]::NewGuid().ToString('N'))"
    try {
        Add-Type -AssemblyName System.Net.Http
        $handler = [Net.Http.HttpClientHandler]::new()
        $client = [Net.Http.HttpClient]::new($handler)
        $cancellation = [Threading.CancellationTokenSource]::new(
            [TimeSpan]::FromMinutes(15)
        )
        try {
            $response = $client.GetAsync(
                $Uri,
                [Net.Http.HttpCompletionOption]::ResponseHeadersRead,
                $cancellation.Token
            ).GetAwaiter().GetResult()
            $null = $response.EnsureSuccessStatusCode()
            $input = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
            $output = [IO.File]::Open(
                $partial,
                [IO.FileMode]::CreateNew,
                [IO.FileAccess]::Write,
                [IO.FileShare]::None
            )
            try {
                $buffer = [byte[]]::new(1048576)
                $total = [int64]0
                while ($true) {
                    $read = $input.ReadAsync(
                        $buffer,
                        0,
                        $buffer.Length,
                        $cancellation.Token
                    ).GetAwaiter().GetResult()
                    if ($read -eq 0) {
                        break
                    }
                    $output.Write($buffer, 0, $read)
                    $total += $read
                    if ($total -gt 2147483648) {
                        throw "A CUDA toolchain download exceeded the two GiB safety limit."
                    }
                }
            } finally {
                $output.Dispose()
                $input.Dispose()
                $response.Dispose()
            }
        } finally {
            $cancellation.Dispose()
            $client.Dispose()
            $handler.Dispose()
        }
        if ((Get-FileSha256 -Path $partial) -ne $Sha256) {
            throw "A downloaded CUDA toolchain artifact failed integrity validation."
        }
        Move-Item -LiteralPath $partial -Destination $Destination
    } finally {
        Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
    }
}

function Install-ToolchainEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$EnvironmentRoot,
        [Parameter(Mandatory = $true)]$Lock,
        [switch]$Offline
    )

    if (Test-Path -LiteralPath $EnvironmentRoot) {
        throw "The existing CUDA toolchain root is incomplete; use a clean root."
    }

    $bootstrapRoot = "$EnvironmentRoot.bootstrap"
    Assert-AsciiPath -Path $bootstrapRoot
    $downloadsRoot = Join-Path $bootstrapRoot "downloads"
    $packagesRoot = Join-Path $bootstrapRoot "packages"
    $mambaRoot = Join-Path $bootstrapRoot "mamba-root"
    $micromambaArchive = Join-Path $downloadsRoot ([string]$Lock.micromamba.archive)
    $micromambaExe = Join-Path $bootstrapRoot "micromamba.exe"

    Save-VerifiedDownload `
        -Uri ([string]$Lock.micromamba.url) `
        -Destination $micromambaArchive `
        -Sha256 ([string]$Lock.micromamba.sha256) `
        -Offline:$Offline

    if (-not (Test-Path -LiteralPath $micromambaExe -PathType Leaf)) {
        if ([string]$Lock.micromamba.archive -notmatch '\.exe$') {
            throw "The pinned Windows micromamba artifact must be an executable."
        }
        if (
            (Get-FileSha256 -Path $micromambaArchive) -ne
            [string]$Lock.micromamba.executable_sha256
        ) {
            throw "The micromamba executable failed integrity validation."
        }
        Copy-Item -LiteralPath $micromambaArchive -Destination $micromambaExe
    } elseif (
        (Get-FileSha256 -Path $micromambaExe) -ne [string]$Lock.micromamba.executable_sha256
    ) {
        throw "The cached micromamba executable failed integrity validation."
    }

    New-Item -ItemType Directory -Path $packagesRoot -Force | Out-Null
    $packagePaths = @()
    foreach ($package in @($Lock.packages)) {
        $fileName = [IO.Path]::GetFileName(([Uri][string]$package.url).AbsolutePath)
        if ($fileName -cnotmatch '^[a-zA-Z0-9._-]+\.conda$') {
            throw "The CUDA package archive name is unsafe."
        }
        $packagePath = Join-Path $packagesRoot $fileName
        Save-VerifiedDownload `
            -Uri ([string]$package.url) `
            -Destination $packagePath `
            -Sha256 ([string]$package.sha256) `
            -Offline:$Offline
        $packagePaths += $packagePath
    }

    $explicitFile = Join-Path $bootstrapRoot "explicit-$([Guid]::NewGuid().ToString('N')).txt"
    $partialEnvironment = "$EnvironmentRoot.partial-$([Guid]::NewGuid().ToString('N'))"
    try {
        $lines = @("@EXPLICIT") + @($packagePaths | ForEach-Object {
            ([Uri][IO.Path]::GetFullPath($_)).AbsoluteUri
        })
        [IO.File]::WriteAllLines($explicitFile, $lines, [Text.UTF8Encoding]::new($false))

        $previousMambaRoot = [Environment]::GetEnvironmentVariable("MAMBA_ROOT_PREFIX", "Process")
        $previousParser = [Environment]::GetEnvironmentVariable(
            "MAMBA_EXPERIMENTAL_REPODATA_PARSING",
            "Process"
        )
        $previousZst = [Environment]::GetEnvironmentVariable("MAMBA_REPODATA_USE_ZST", "Process")
        try {
            [Environment]::SetEnvironmentVariable("MAMBA_ROOT_PREFIX", $mambaRoot, "Process")
            [Environment]::SetEnvironmentVariable(
                "MAMBA_EXPERIMENTAL_REPODATA_PARSING",
                "false",
                "Process"
            )
            [Environment]::SetEnvironmentVariable("MAMBA_REPODATA_USE_ZST", "false", "Process")
            $previousErrorAction = $ErrorActionPreference
            try {
                $ErrorActionPreference = "Continue"
                $micromambaOutput = @(
                    & $micromambaExe `
                        --no-rc `
                        create `
                        --yes `
                        --prefix $partialEnvironment `
                        --file $explicitFile 2>&1
                )
                $micromambaExitCode = $LASTEXITCODE
            } finally {
                $ErrorActionPreference = $previousErrorAction
            }
            if ($micromambaExitCode -ne 0) {
                throw "The pinned CUDA toolchain environment could not be created."
            }
        } finally {
            [Environment]::SetEnvironmentVariable("MAMBA_ROOT_PREFIX", $previousMambaRoot, "Process")
            [Environment]::SetEnvironmentVariable(
                "MAMBA_EXPERIMENTAL_REPODATA_PARSING",
                $previousParser,
                "Process"
            )
            [Environment]::SetEnvironmentVariable("MAMBA_REPODATA_USE_ZST", $previousZst, "Process")
        }

        Assert-ToolchainEnvironment -EnvironmentRoot $partialEnvironment -Lock $Lock
        Move-Item -LiteralPath $partialEnvironment -Destination $EnvironmentRoot
    } finally {
        Remove-Item -LiteralPath $explicitFile -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $partialEnvironment -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($LockPath)) {
    $LockPath = Join-Path $runtimeRoot "cuda-toolchain.lock.json"
}
$resolvedLockPath = [IO.Path]::GetFullPath($LockPath)
if (-not (Test-Path -LiteralPath $resolvedLockPath -PathType Leaf)) {
    throw "The CUDA toolchain lock is missing."
}
$lock = Get-Content -LiteralPath $resolvedLockPath -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-Lock -Lock $lock

if ($ValidateLockOnly) {
    [pscustomobject]@{
        schema_version = 1
        lock = $resolvedLockPath
        package_count = @($lock.packages).Count
    } | ConvertTo-Json -Compress
    return
}

if ([string]::IsNullOrWhiteSpace($ToolchainRoot)) {
    $ToolchainRoot = [Environment]::GetEnvironmentVariable(
        "FAIRY_OMNI_CUDA_TOOLCHAIN_ROOT",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($ToolchainRoot)) {
    $localAppData = [Environment]::GetFolderPath("LocalApplicationData")
    if ([string]::IsNullOrWhiteSpace($localAppData)) {
        throw "Local Application Data is unavailable for the CUDA toolchain cache."
    }
    $ToolchainRoot = Join-Path $localAppData "Fairy\build-tools\omni-cuda\$([string]$lock.cache_namespace)"
}
$resolvedToolchainRoot = [IO.Path]::GetFullPath($ToolchainRoot)
Assert-AsciiPath -Path $resolvedToolchainRoot

if (-not (Test-Path -LiteralPath $resolvedToolchainRoot -PathType Container)) {
    Install-ToolchainEnvironment `
        -EnvironmentRoot $resolvedToolchainRoot `
        -Lock $lock `
        -Offline:$Offline
}
Assert-ToolchainEnvironment -EnvironmentRoot $resolvedToolchainRoot -Lock $lock

$runtimeComponents = @($lock.runtime_components | ForEach-Object {
    [pscustomobject]@{
        name = [string]$_.name
        source = Join-Path $resolvedToolchainRoot "Library\bin\$([string]$_.name)"
        package = [string]$_.package
        version = [string]$_.version
        license = [string]$_.license
        bytes = [int64]$_.bytes
        sha256 = [string]$_.sha256
    }
})
[pscustomobject]@{
    schema_version = 1
    root = $resolvedToolchainRoot
    bin = Join-Path $resolvedToolchainRoot "Library\bin"
    cmake_root = Join-Path $resolvedToolchainRoot "Library"
    nvcc = Join-Path $resolvedToolchainRoot "Library\bin\nvcc.exe"
    ninja = Join-Path $resolvedToolchainRoot "Library\bin\ninja.exe"
    runtime_components = $runtimeComponents
} | ConvertTo-Json -Depth 5 -Compress
