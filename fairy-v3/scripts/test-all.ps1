[CmdletBinding()]
param(
    [switch]$SkipDocker,
    [switch]$RequireWslSandbox
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$CoreRoot = Join-Path $Root "core"
$CloudRoot = Join-Path $Root "cloud"
$DesktopRoot = Join-Path $Root "desktop"
$RustRoot = Join-Path $DesktopRoot "src-tauri"
$UvCandidate = "C:\Python313\Scripts\uv.exe"
$Uv = if (Test-Path -LiteralPath $UvCandidate) { $UvCandidate } else { "uv" }

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

Invoke-Step "Boundaries: check_boundaries.py" $Root $Uv @(
    "run", "--project", "core", "python", "scripts/check_boundaries.py", "."
)

Invoke-Step "Core: uv lock --check" $CoreRoot $Uv @("lock", "--check")
Invoke-Step "Core: ruff format --check" $CoreRoot $Uv @(
    "run", "ruff", "format", "--check", "src", "tests"
)
Invoke-Step "Core: ruff check" $CoreRoot $Uv @(
    "run", "ruff", "check", "src", "tests"
)
Invoke-Step "Core: pytest" $CoreRoot $Uv @("run", "pytest")

Invoke-Step "Cloud: uv lock --check" $CloudRoot $Uv @("lock", "--check")
Invoke-Step "Cloud: ruff format --check" $CloudRoot $Uv @(
    "run", "ruff", "format", "--check", "src", "tests"
)
Invoke-Step "Cloud: ruff check" $CloudRoot $Uv @(
    "run", "ruff", "check", "src", "tests"
)
Invoke-Step "Cloud: pytest unit" $CloudRoot $Uv @(
    "run", "pytest", "-m", "not integration"
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

if ($RequireWslSandbox) {
    $WslProbeScript = @'
import json
from fairy_core.runtime.wsl_health import WslSandboxHealthProbe

health = WslSandboxHealthProbe().health()
print(json.dumps({
    "available": health.available,
    "executor": health.executor,
    "version": health.version,
    "error_code": health.error_code,
    "diagnostics": list(health.diagnostics),
}, sort_keys=True))
raise SystemExit(0 if health.available else 1)
'@
    Invoke-Step "WSL: FairySandbox attestation" $CoreRoot $Uv @(
        "run", "python", "-c", $WslProbeScript
    )
    Invoke-Step "WSL-required Runtime: static Preview start/status/stop" $RustRoot "cargo" @(
        "test", "-p", "fairy-local-worker", "--test", "preview_recovery"
    )
    Write-Host "`nFairySandbox attestation and the real local static Preview lifecycle passed."
}
else {
    Write-Host "`nWSL sandbox verification skipped; pass -RequireWslSandbox to require a real FairySandbox attestation and static Preview lifecycle gate."
}

Invoke-Step "Desktop: npm test -- --run" $DesktopRoot "npm" @("test", "--", "--run")
Invoke-Step "Desktop: npm run build" $DesktopRoot "npm" @("run", "build")
Invoke-Step "Contracts: generate-contracts.ps1" $Root "powershell" @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "scripts/generate-contracts.ps1"
)
Invoke-Step "Contracts: git diff --exit-code" $Root "git" @(
    "diff",
    "--exit-code",
    "--",
    "contracts/openapi.json",
    "desktop/src/core/generated/api.d.ts"
)

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
        Invoke-Step "Docker: docker compose integration (PostgreSQL 18.4, S3, RLS, memory retrieval, runtime Preview)" $Root "docker" (
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
    Write-Host "`nDocker explicitly disabled with -SkipDocker: PostgreSQL/S3 integration tests skipped; memory retrieval and runtime Preview integration was not executed."
}
else {
    Write-Host "`nDocker CLI or daemon unavailable: real PostgreSQL/S3 integration tests skipped; memory retrieval and runtime Preview integration was not executed."
}

Write-Host "`nAll available Fairy V3 verification gates passed."
