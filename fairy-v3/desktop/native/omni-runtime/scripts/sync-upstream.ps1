[CmdletBinding()]
param(
    [string]$DependencyRoot,
    [string]$LockPath,
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & git -c "safe.directory=$WorkingDirectory" -C $WorkingDirectory @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "git $($Arguments -join ' ') failed in '$WorkingDirectory':`n$($output -join "`n")"
    }
    return @($output)
}

function Resolve-ContainedPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BasePath,
        [Parameter(Mandatory = $true)]
        [string]$CandidatePath
    )

    $base = [IO.Path]::GetFullPath($BasePath).TrimEnd('\', '/')
    $candidate = [IO.Path]::GetFullPath($CandidatePath).TrimEnd('\', '/')
    $prefix = "$base$([IO.Path]::DirectorySeparatorChar)"
    if (-not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Dependency path '$candidate' escapes the allowed root '$base'."
    }
    return $candidate
}

function Assert-NoReparsePoint {
    param([Parameter(Mandatory = $true)][string]$Path)

    $current = [IO.Path]::GetFullPath($Path)
    while (Test-Path -LiteralPath $current) {
        $item = Get-Item -LiteralPath $current -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing reparse-point path '$current'."
        }
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrEmpty($parent) -or $parent -eq $current) {
            break
        }
        $current = $parent
    }
}

$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($LockPath)) {
    $LockPath = Join-Path $runtimeRoot "upstream.lock.json"
}
$lockPath = [IO.Path]::GetFullPath($LockPath)
$lock = Get-Content -LiteralPath $lockPath -Raw -Encoding UTF8 | ConvertFrom-Json
$allowedRoot = [IO.Path]::GetFullPath((Join-Path $runtimeRoot "_deps"))

if ([string]::IsNullOrWhiteSpace($DependencyRoot)) {
    $DependencyRoot = Join-Path $allowedRoot "llama.cpp-omni-source"
}

$dependency = Resolve-ContainedPath -BasePath $allowedRoot -CandidatePath $DependencyRoot
Assert-NoReparsePoint -Path $dependency

if (-not (Test-Path -LiteralPath $allowedRoot)) {
    New-Item -ItemType Directory -Path $allowedRoot | Out-Null
}

$repository = [string]$lock.upstream.repository
$revision = [string]$lock.upstream.revision

if (-not (Test-Path -LiteralPath $dependency)) {
    if ($Offline) {
        throw "The pinned upstream is missing and offline mode was requested."
    }
    New-Item -ItemType Directory -Path $dependency | Out-Null
    & git -c "safe.directory=$dependency" -C $dependency init --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to initialize dependency repository '$dependency'."
    }
    Invoke-Git -WorkingDirectory $dependency remote add origin $repository | Out-Null
}

Assert-NoReparsePoint -Path $dependency
if (-not (Test-Path -LiteralPath (Join-Path $dependency ".git"))) {
    throw "Dependency root '$dependency' is not a Git repository."
}

$origin = (Invoke-Git -WorkingDirectory $dependency remote get-url origin | Select-Object -First 1).Trim()
if (-not [string]::Equals($origin, $repository, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Upstream origin mismatch. Expected '$repository', got '$origin'."
}

    $dirty = @(Invoke-Git -WorkingDirectory $dependency status --porcelain=v1 --untracked-files=all |
        Where-Object { $_ -notmatch "^warning: unable to access " })
if ($dirty.Count -ne 0) {
    throw "Pinned upstream is dirty; refusing to overwrite local changes."
}

if (-not $Offline) {
    Invoke-Git -WorkingDirectory $dependency fetch --quiet --depth=1 origin $revision | Out-Null
    Invoke-Git -WorkingDirectory $dependency checkout --quiet --detach FETCH_HEAD | Out-Null
} else {
    try {
        Invoke-Git -WorkingDirectory $dependency checkout --quiet --detach $revision | Out-Null
    } catch {
        throw "Pinned upstream revision '$revision' is unavailable."
    }
}

$head = (Invoke-Git -WorkingDirectory $dependency rev-parse HEAD | Select-Object -First 1).Trim().ToLowerInvariant()
if ($head -ne $revision.ToLowerInvariant()) {
    throw "Pinned upstream revision mismatch. Expected '$revision', got '$head'."
}

$dirtyAfterCheckout = @(Invoke-Git -WorkingDirectory $dependency status --porcelain=v1 --untracked-files=all |
    Where-Object { $_ -notmatch "^warning: unable to access " })
if ($dirtyAfterCheckout.Count -ne 0) {
    throw "Pinned upstream became dirty after checkout."
}

[pscustomobject]@{
    source_root = $dependency
    repository = $repository
    revision = $head
    detached = $true
} | ConvertTo-Json -Compress
