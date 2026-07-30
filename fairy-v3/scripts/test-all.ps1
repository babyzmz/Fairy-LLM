[CmdletBinding()]
param(
    [switch]$SkipDocker,
    [switch]$RequireWslSandbox
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$CoreRoot = Join-Path $Root "core"
$CapabilitiesRoot = Join-Path $Root "capabilities"
$CloudRoot = Join-Path $Root "cloud"
$DesktopRoot = Join-Path $Root "desktop"
$RustRoot = Join-Path $DesktopRoot "src-tauri"
$UvCandidate = "C:\Python313\Scripts\uv.exe"
$Uv = if (Test-Path -LiteralPath $UvCandidate) { $UvCandidate } else { "uv" }
$ReleaseCacheRoot = Join-Path ([System.IO.Path]::GetTempPath()) "fairy-v3-release"
New-Item -ItemType Directory -Force -Path $ReleaseCacheRoot | Out-Null
$env:HYPOTHESIS_STORAGE_DIRECTORY = Join-Path $ReleaseCacheRoot "hypothesis"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:UV_CACHE_DIR = Join-Path $ReleaseCacheRoot "uv"

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    Write-Host "`n==> $Label"
    Push-Location -LiteralPath $WorkingDirectory
    try {
        & $Command @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Label failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

function Test-DockerAvailable {
    $DockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $DockerCommand) {
        return $false
    }

    Write-Host "`n==> Docker: docker version"
    & $DockerCommand.Source version *> $null
    return $LASTEXITCODE -eq 0
}

function New-DesktopPathAlias {
    if ($Root -cmatch '^[\x00-\x7F]+$' -and -not $Root.Contains("~")) {
        return [PSCustomObject]@{ Root = $Root; Drive = $null }
    }

    $SubstCommand = Get-Command subst.exe -ErrorAction SilentlyContinue
    if ($null -eq $SubstCommand) {
        Write-Warning "subst.exe is unavailable; desktop tools will use the original path."
        return [PSCustomObject]@{ Root = $Root; Drive = $null }
    }
    foreach ($Letter in @("Q", "R", "S", "T", "U", "W", "X", "Y", "Z")) {
        $Drive = "${Letter}:"
        if (Test-Path -LiteralPath "${Drive}\") {
            continue
        }
        & $SubstCommand.Source $Drive $Root
        if ($LASTEXITCODE -eq 0) {
            Write-Host "`nDesktop tools use ASCII workspace alias ${Drive}\"
            return [PSCustomObject]@{ Root = "${Drive}\"; Drive = $Drive }
        }
    }
    throw "No free drive letter is available for the desktop workspace alias"
}

Invoke-Step "Scripts: ruff format --check" $Root $Uv @(
    "run",
    "--project",
    "core",
    "ruff",
    "format",
    "--check",
    "--no-cache",
    "scripts/check_boundaries.py",
    "scripts/check-release-documents.py",
    "scripts/check_release_bundle.py",
    "scripts/test_release_bundle.py",
    "scripts/release_performance.py"
)
Invoke-Step "Scripts: ruff check" $Root $Uv @(
    "run",
    "--project",
    "core",
    "ruff",
    "check",
    "--no-cache",
    "scripts/check_boundaries.py",
    "scripts/check-release-documents.py",
    "scripts/check_release_bundle.py",
    "scripts/test_release_bundle.py",
    "scripts/release_performance.py"
)

Invoke-Step "Boundaries: check_boundaries.py" $Root $Uv @(
    "run", "--project", "core", "python", "scripts/check_boundaries.py", "."
)
Invoke-Step "Release: disclosures" $Root $Uv @(
    "run", "--project", "core", "python", "scripts/check-release-documents.py"
)
Invoke-Step "Release: bundle policy" $Root $Uv @(
    "run", "--project", "core", "python", "scripts/check_release_bundle.py"
)
Invoke-Step "Release: bundle policy fixtures" $Root $Uv @(
    "run", "--project", "core", "python", "scripts/test_release_bundle.py"
)
Invoke-Step "Release: Omni packaging policy" $Root "powershell" @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts/test-omni-release-policy.ps1"
)
Invoke-Step "Release: Omni CUDA toolchain policy" $Root "powershell" @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "desktop/native/omni-runtime/scripts/cuda-toolchain.tests.ps1"
)
Invoke-Step "Release: Omni runtime staging policy" $Root "powershell" @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts/test-omni-runtime-staging.ps1"
)
Invoke-Step "Release: Voice license policy" $Root "powershell" @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts/test-voice-release-licenses.ps1"
)
Invoke-Step "Voice: runtime policy" $Root "powershell" @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts/test-voice-runtime-policy.ps1"
)
Invoke-Step "Presence: input ownership guard" $Root "powershell" @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts/test-presence-input-ownership-guard.ps1"
)
Invoke-Step "Release: fail-closed evidence fixtures" $Root "powershell" @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts/test-realtime-companion-beta-release-fixtures.ps1"
)

Invoke-Step "Sandbox runner: ruff format --check" $Root $Uv @(
    "run", "--project", "core", "ruff", "format", "--check",
    "--no-cache",
    "sandbox/runner", "sandbox/tests", "scripts/verify_wsl_sandbox.py",
    "scripts/verify_wsl_scratch_runtime.py"
)
Invoke-Step "Sandbox runner: ruff check" $Root $Uv @(
    "run", "--project", "core", "ruff", "check",
    "--no-cache",
    "sandbox/runner", "sandbox/tests", "scripts/verify_wsl_sandbox.py",
    "scripts/verify_wsl_scratch_runtime.py"
)
Invoke-Step "Sandbox runner: pytest" $Root $Uv @(
    "run", "--project", "core", "python", "-m", "pytest",
    "-p", "no:cacheprovider", "sandbox/tests"
)

Invoke-Step "Core: uv lock --check" $CoreRoot $Uv @("lock", "--check")
Invoke-Step "Core: ruff format --check" $CoreRoot $Uv @(
    "run", "ruff", "format", "--check", "--no-cache", "src", "tests"
)
Invoke-Step "Core: ruff check" $CoreRoot $Uv @(
    "run", "ruff", "check", "--no-cache", "src", "tests"
)
Invoke-Step "Core: pytest" $CoreRoot $Uv @(
    "run", "python", "-m", "pytest", "-p", "no:cacheprovider"
)

Invoke-Step "Capabilities: uv lock --check" $CapabilitiesRoot $Uv @("lock", "--check")
Invoke-Step "Capabilities: ruff format --check" $CapabilitiesRoot $Uv @(
    "run", "ruff", "format", "--check", "--no-cache", "src", "tests"
)
Invoke-Step "Capabilities: ruff check" $CapabilitiesRoot $Uv @(
    "run", "ruff", "check", "--no-cache", "src", "tests"
)
Invoke-Step "Capabilities: pytest" $CapabilitiesRoot $Uv @(
    "run", "python", "-m", "pytest", "-p", "no:cacheprovider"
)

Invoke-Step "Cloud: uv lock --check" $CloudRoot $Uv @("lock", "--check")
Invoke-Step "Cloud: ruff format --check" $CloudRoot $Uv @(
    "run", "ruff", "format", "--check", "--no-cache", "src", "tests"
)
Invoke-Step "Cloud: ruff check" $CloudRoot $Uv @(
    "run", "ruff", "check", "--no-cache", "src", "tests"
)
Invoke-Step "Cloud: pytest unit" $CloudRoot $Uv @(
    "run", "python", "-m", "pytest", "-p", "no:cacheprovider",
    "-m", "not integration"
)
Invoke-Step "Cloud: alembic upgrade head --sql" $CloudRoot $Uv @(
    "run", "alembic", "upgrade", "head", "--sql"
)
Invoke-Step "Cloud: alembic downgrade head:base --sql" $CloudRoot $Uv @(
    "run", "alembic", "downgrade", "head:base", "--sql"
)

Invoke-Step "Rust: cargo fmt --check" $RustRoot "cargo" @(
    "fmt", "--all", "--", "--check"
)
Invoke-Step "Rust: cargo clippy" $RustRoot "cargo" @(
    "clippy", "--workspace", "--all-targets", "--all-features", "--", "-D", "warnings"
)
Invoke-Step "Rust: cargo test" $RustRoot "cargo" @(
    "test", "--workspace", "--all-targets", "--all-features"
)
Invoke-Step "Rust: build desktop worker for release composition" $RustRoot "cargo" @(
    "build", "--bin", "fairy"
)

if ($RequireWslSandbox) {
    $WslProbeScript = @'
import json
from fairy_core.runtime.wsl_health import WslSandboxHealthProbe

health = WslSandboxHealthProbe().health()
print(json.dumps(dict(
    available=health.available,
    executor=health.executor,
    version=health.version,
    error_code=health.error_code,
    diagnostics=list(health.diagnostics),
), sort_keys=True))
raise SystemExit(0 if health.available else 1)
'@
    Invoke-Step "WSL: FairySandbox attestation" $CoreRoot $Uv @(
        "run", "python", "-c", $WslProbeScript
    )
    Invoke-Step "WSL: structured Sandbox execution" $Root $Uv @(
        "run", "--project", "core", "python", "scripts/verify_wsl_sandbox.py"
    )
    Invoke-Step "WSL: projectless chat Workspace Runtime" $Root $Uv @(
        "run", "--project", "core", "python", "scripts/verify_wsl_scratch_runtime.py"
    )
    Invoke-Step "WSL-required Runtime: static Preview start/status/stop" $RustRoot "cargo" @(
        "test", "-p", "fairy-local-worker", "--test", "preview_recovery"
    )
    Write-Host "`nFairySandbox attestation and the real local static Preview lifecycle passed."
}
else {
    Write-Host "`nWSL sandbox verification skipped; pass -RequireWslSandbox to require a real FairySandbox attestation and static Preview lifecycle gate."
}

Invoke-Step "Desktop release: build and probe bundled Core" $Root "powershell" @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts/build-core-sidecar.ps1"
)
Invoke-Step "Desktop release: verify pinned MinGit runtime" $Root "powershell" @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts/prepare-git-runtime.ps1"
)
Invoke-Step "Desktop release: bundled Core, Rust worker, and MinGit composition" $Root "powershell" @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts/test-release-composition.ps1"
)

$DesktopAlias = New-DesktopPathAlias
$DesktopTestRoot = Join-Path $DesktopAlias.Root "desktop"
try {
    Invoke-Step "Desktop: npm test" $DesktopTestRoot "npm" @("test")
    Invoke-Step "Desktop: npm run e2e" $DesktopTestRoot "npm" @("run", "e2e")
    Invoke-Step "Desktop: npm run build" $DesktopTestRoot "npm" @("run", "build")
}
finally {
    if ($null -ne $DesktopAlias.Drive) {
        & subst.exe $DesktopAlias.Drive /D
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Could not remove desktop workspace alias $($DesktopAlias.Drive)"
        }
    }
}
Invoke-Step "Performance: Core ready <= 3s and initial renderer gzip <= 800 KiB" $Root $Uv @(
    "run",
    "--project",
    "capabilities",
    "python",
    "scripts/release_performance.py",
    "--desktop-dist",
    "desktop/dist"
)
$ContractPaths = @(
    (Join-Path $Root "contracts/openapi.json"),
    (Join-Path $Root "contracts/rpc-methods.json"),
    (Join-Path $Root "desktop/src/core/generated/api.d.ts"),
    (Join-Path $Root "desktop/src/core/generated/rpcMethods.ts")
)
$ContractHashes = @{}
foreach ($ContractPath in $ContractPaths) {
    $ContractHashes[$ContractPath] = (Get-FileHash -Algorithm SHA256 -LiteralPath $ContractPath).Hash
}
Invoke-Step "Contracts: generate-contracts.ps1" $Root "powershell" @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts/generate-contracts.ps1"
)
foreach ($ContractPath in $ContractPaths) {
    $GeneratedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ContractPath).Hash
    if ($GeneratedHash -ne $ContractHashes[$ContractPath]) {
        throw "Generated contract drift detected: $ContractPath"
    }
}
Write-Host "`nContracts: generated files unchanged"
Invoke-Step "Git: diff --check" $Root "git" @("diff", "--check")

$DockerAvailable = $false
if (-not $SkipDocker) {
    $DockerAvailable = Test-DockerAvailable
}

if ($DockerAvailable) {
    $ComposeArguments = @(
        "compose",
        "--project-name",
        "fairy-v3-verify",
        "-f",
        "cloud/compose.yaml",
        "--profile",
        "test"
    )
    try {
        Invoke-Step "Docker: docker compose integration (PostgreSQL 18.4/S3/RLS/recovery/outbox/two-device)" $Root "docker" (
            $ComposeArguments + @("run", "--build", "--rm", "integration")
        )
    }
    finally {
        Write-Host "`n==> Docker: cleanup"
        & docker @ComposeArguments down --remove-orphans
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Docker integration cleanup exited with code $LASTEXITCODE"
        }
    }
}
elseif ($SkipDocker) {
    Write-Host "`nDocker explicitly disabled with -SkipDocker: PostgreSQL/S3 integration tests skipped; recovery, capability Outbox, two-device sync, memory retrieval, documents, evidence, and runtime Preview integration was not executed."
}
else {
    Write-Host "`nDocker CLI or daemon unavailable: real PostgreSQL/S3 integration tests skipped; recovery, capability Outbox, two-device sync, memory retrieval, documents, evidence, and runtime Preview integration was not executed."
}

Write-Host "`nAll available Fairy V3 verification gates passed."
