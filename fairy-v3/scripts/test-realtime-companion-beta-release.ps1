[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$EvidenceDirectory,
    [Parameter(Mandatory)]
    [string]$BundleDirectory,
    [Parameter(Mandatory)]
    [string]$OutputPath,
    [ValidateRange(1, 365)]
    [int]$MaxEvidenceAgeDays = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$requiredTopLevel = @(
    "schema_version",
    "product",
    "version",
    "phase_acceptance",
    "regression",
    "soak",
    "native",
    "performance",
    "privacy",
    "installer",
    "security",
    "artifacts"
)
$requiredDomainProperties = @{
    phase_acceptance = @("status", "observed_at", "phase_0_through_7")
    regression = @(
        "status", "observed_at", "core", "rust", "typescript", "vitest", "playwright"
    )
    soak = @(
        "status", "observed_at", "observed_duration_seconds", "context_rotations",
        "pause_resume_cycles", "window_switches", "backend", "terminal_session_status",
        "digest_count", "sidecar_recovery", "second_failure_quarantined",
        "process_cleanup_confirmed"
    )
    native = @(
        "status", "observed_at", "proposal_authority", "resource_projection",
        "crash_recovery", "quarantine", "companion_reload", "no_worker_preheat"
    )
    performance = @(
        "status", "observed_at", "model_ready_progress", "visual_p95_ms",
        "speech_to_text_p95_ms", "voice_start_p95_ms", "barge_in_p95_ms",
        "queues_bounded", "latest_frame_only", "median_gpu_frame_time_impact_percent",
        "game_one_percent_low_impact_percent", "no_game_or_desktop_crash"
    )
    privacy = @(
        "status", "observed_at", "selected_window_only", "sensitive_window_pause",
        "cloud_scope_disclosed", "local_scope_disclosed", "pause_stops_frames",
        "no_raw_media_on_disk", "no_content_in_logs", "telemetry_default_off"
    )
    installer = @(
        "status", "observed_at", "install", "start", "upgrade", "uninstall",
        "user_data_preserved", "cold_start_no_preheat", "manifest_relative_path"
    )
    security = @(
        "status", "observed_at", "malware_scan_passed", "signed", "signature_verified"
    )
}
$forbiddenKeys = @(
    "api_key",
    "audio",
    "caption",
    "credential",
    "image",
    "prompt",
    "provider_payload",
    "question",
    "raw_window_title",
    "reasoning",
    "screenshot",
    "transcript"
)
$gates = [System.Collections.Generic.List[object]]::new()

function Add-Gate([string]$Name, [bool]$Passed, [string]$Reason) {
    $status = "blocked"
    if ($Passed) { $status = "passed" }
    $gates.Add([ordered]@{
        name = $Name
        status = $status
        reason = $Reason
    })
}

function Add-ResultGate(
    [string]$Name,
    [bool]$Passed,
    [string]$PassedReason,
    [string]$BlockedReason
) {
    $reason = $BlockedReason
    if ($Passed) { $reason = $PassedReason }
    Add-Gate $Name $Passed $reason
}

function Assert-FullyQualifiedPath([string]$Path, [string]$Label) {
    $root = [System.IO.Path]::GetPathRoot($Path)
    if (-not [System.IO.Path]::IsPathRooted($Path) -or
        [string]::IsNullOrWhiteSpace($root) -or
        -not ($root.EndsWith("\") -or $root.EndsWith("/"))) {
        throw "$Label must be fully qualified"
    }
}

function Assert-ExactProperties($Value, [string[]]$Required, [string]$Label) {
    $actual = @($Value.PSObject.Properties.Name)
    foreach ($name in $Required) {
        if ($actual -notcontains $name) {
            throw "$Label is missing property '$name'"
        }
    }
    $unexpected = @($actual | Where-Object { $Required -notcontains $_ })
    if ($unexpected.Count -gt 0) {
        throw "$Label contains unexpected properties: $($unexpected -join ', ')"
    }
}

function Assert-SafeEvidence($Value, [string]$Path = "evidence") {
    if ($null -eq $Value) { return }
    if ($Value -is [string]) {
        if ($Value.Length -gt 512) {
            throw "$Path contains an oversized string"
        }
        return
    }
    if ($Value -is [System.Collections.IEnumerable] -and
        $Value -isnot [System.Management.Automation.PSCustomObject]) {
        foreach ($item in $Value) {
            Assert-SafeEvidence $item "$Path[]"
        }
        return
    }
    foreach ($property in $Value.PSObject.Properties) {
        $lowered = $property.Name.ToLowerInvariant()
        if ($forbiddenKeys -contains $lowered) {
            throw "$Path contains forbidden content field '$($property.Name)'"
        }
        Assert-SafeEvidence $property.Value "$Path.$($property.Name)"
    }
}

function Test-FreshPassedGate($Value, [string]$Name, [datetime]$NowUtc) {
    if ([string]$Value.status -ne "passed") {
        Add-Gate $Name $false "${Name}_NOT_PASSED"
        return $false
    }
    $observed = [datetime]::MinValue
    if (-not [datetime]::TryParse(
        [string]$Value.observed_at,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::AdjustToUniversal,
        [ref]$observed
    )) {
        Add-Gate $Name $false "${Name}_TIMESTAMP_INVALID"
        return $false
    }
    $age = $NowUtc - $observed.ToUniversalTime()
    if ($age.TotalSeconds -lt -300 -or $age.TotalDays -gt $MaxEvidenceAgeDays) {
        Add-Gate $Name $false "${Name}_EVIDENCE_STALE"
        return $false
    }
    return $true
}

function Test-AllTrue($Value, [string[]]$Names) {
    foreach ($name in $Names) {
        if ($Value.$name -ne $true) { return $false }
    }
    return $true
}

function Resolve-RelativeArtifact(
    [string]$Root,
    [string]$RelativePath,
    [string]$Label
) {
    if ([System.IO.Path]::IsPathRooted($RelativePath)) {
        throw "$Label must use a relative path"
    }
    $normalized = $RelativePath.Replace("/", [System.IO.Path]::DirectorySeparatorChar)
    if ([string]::IsNullOrWhiteSpace($normalized) -or
        $normalized.Split([System.IO.Path]::DirectorySeparatorChar) -contains "..") {
        throw "$Label contains an unsafe relative path"
    }
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $Root $normalized))
    if (-not $candidate.StartsWith(
        $Root + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "$Label escapes the bundle directory"
    }
    return $candidate
}

Assert-FullyQualifiedPath $EvidenceDirectory "EvidenceDirectory"
Assert-FullyQualifiedPath $BundleDirectory "BundleDirectory"
Assert-FullyQualifiedPath $OutputPath "OutputPath"
$evidenceRoot = [System.IO.Path]::GetFullPath($EvidenceDirectory).TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar
)
$bundleRoot = [System.IO.Path]::GetFullPath($BundleDirectory).TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar
)
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
if (-not (Test-Path -LiteralPath $evidenceRoot -PathType Container)) {
    throw "EvidenceDirectory does not exist"
}
if (-not (Test-Path -LiteralPath $bundleRoot -PathType Container)) {
    throw "BundleDirectory does not exist"
}
if (Test-Path -LiteralPath $resolvedOutput) {
    throw "Refusing to overwrite an existing release-gate report"
}
$outputParent = Split-Path -Parent $resolvedOutput
if (-not (Test-Path -LiteralPath $outputParent -PathType Container)) {
    throw "OutputPath parent directory does not exist"
}

$evidencePath = Join-Path $evidenceRoot "realtime-beta-release-evidence.json"
$manifest = $null
$fatalReason = $null
try {
    if (-not (Test-Path -LiteralPath $evidencePath -PathType Leaf)) {
        throw "release evidence manifest is missing"
    }
    $manifest = Get-Content -Raw -LiteralPath $evidencePath | ConvertFrom-Json
    Assert-ExactProperties $manifest $requiredTopLevel "release evidence"
    foreach ($domain in $requiredDomainProperties.Keys) {
        Assert-ExactProperties $manifest.$domain $requiredDomainProperties[$domain] $domain
    }
    Assert-SafeEvidence $manifest
    if ([int]$manifest.schema_version -ne 1) {
        throw "release evidence schema is unsupported"
    }
    if ([string]$manifest.product -ne "Fairy") {
        throw "release evidence product is not Fairy"
    }
    if ([string]$manifest.version -notmatch "^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$" -or
        ([string]$manifest.version).Length -gt 32) {
        throw "release evidence version is invalid"
    }
}
catch {
    $fatalReason = "EVIDENCE_INVALID"
    Add-Gate "evidence" $false $fatalReason
}

if ($null -ne $manifest) {
    $nowUtc = [datetime]::UtcNow

    $phaseFresh = Test-FreshPassedGate $manifest.phase_acceptance "phase_acceptance" $nowUtc
    $phasePassed = $phaseFresh -and $manifest.phase_acceptance.phase_0_through_7 -eq $true
    Add-ResultGate "phase_contracts" $phasePassed "PHASE_0_7_ACCEPTED" "PHASE_0_7_INCOMPLETE"

    $regressionFresh = Test-FreshPassedGate $manifest.regression "regression" $nowUtc
    $regressionPassed = $regressionFresh -and (Test-AllTrue $manifest.regression @(
        "core",
        "rust",
        "typescript",
        "vitest",
        "playwright"
    ))
    Add-ResultGate "complete_regression" $regressionPassed `
        "COMPLETE_REGRESSION_PASSED" "REGRESSION_INCOMPLETE"

    $soakFresh = Test-FreshPassedGate $manifest.soak "soak" $nowUtc
    $soakPassed = $soakFresh -and
        [int]$manifest.soak.observed_duration_seconds -ge 14400 -and
        [int]$manifest.soak.context_rotations -ge 20 -and
        [int]$manifest.soak.pause_resume_cycles -ge 10 -and
        [int]$manifest.soak.window_switches -ge 5 -and
        [string]$manifest.soak.backend -eq "local_mini_cpm_o45" -and
        [string]$manifest.soak.terminal_session_status -eq "completed" -and
        [int]$manifest.soak.digest_count -ge 1 -and
        (Test-AllTrue $manifest.soak @(
            "sidecar_recovery",
            "second_failure_quarantined",
            "process_cleanup_confirmed"
        ))
    Add-ResultGate "four_hour_soak" $soakPassed `
        "FOUR_HOUR_SOAK_PASSED" "FOUR_HOUR_SOAK_REQUIRED"

    $nativeFresh = Test-FreshPassedGate $manifest.native "native" $nowUtc
    $nativePassed = $nativeFresh -and (Test-AllTrue $manifest.native @(
        "proposal_authority",
        "resource_projection",
        "crash_recovery",
        "quarantine",
        "companion_reload",
        "no_worker_preheat"
    ))
    Add-ResultGate "native_webview2" $nativePassed `
        "NATIVE_WEBVIEW2_PASSED" "NATIVE_WEBVIEW2_REQUIRED"

    $performanceFresh = Test-FreshPassedGate $manifest.performance "performance" $nowUtc
    $performancePassed = $performanceFresh -and
        $manifest.performance.model_ready_progress -eq $true -and
        [double]$manifest.performance.visual_p95_ms -le 1500 -and
        [double]$manifest.performance.speech_to_text_p95_ms -le 1500 -and
        [double]$manifest.performance.voice_start_p95_ms -le 2000 -and
        [double]$manifest.performance.barge_in_p95_ms -le 120 -and
        [double]$manifest.performance.median_gpu_frame_time_impact_percent -le 10 -and
        [double]$manifest.performance.game_one_percent_low_impact_percent -le 15 -and
        (Test-AllTrue $manifest.performance @(
            "queues_bounded",
            "latest_frame_only",
            "no_game_or_desktop_crash"
        ))
    Add-ResultGate "reference_performance" $performancePassed `
        "REFERENCE_PERFORMANCE_PASSED" "REFERENCE_PERFORMANCE_REQUIRED"

    $privacyFresh = Test-FreshPassedGate $manifest.privacy "privacy" $nowUtc
    $privacyPassed = $privacyFresh -and (Test-AllTrue $manifest.privacy @(
        "selected_window_only",
        "sensitive_window_pause",
        "cloud_scope_disclosed",
        "local_scope_disclosed",
        "pause_stops_frames",
        "no_raw_media_on_disk",
        "no_content_in_logs",
        "telemetry_default_off"
    ))
    Add-ResultGate "privacy" $privacyPassed `
        "PRIVACY_GATES_PASSED" "PRIVACY_EVIDENCE_REQUIRED"

    $installerFresh = Test-FreshPassedGate $manifest.installer "installer" $nowUtc
    $installerPassed = $installerFresh -and (Test-AllTrue $manifest.installer @(
        "install",
        "start",
        "upgrade",
        "uninstall",
        "user_data_preserved",
        "cold_start_no_preheat"
    ))
    Add-ResultGate "installer_lifecycle" $installerPassed `
        "INSTALLER_LIFECYCLE_PASSED" "INSTALLER_LIFECYCLE_REQUIRED"

    $securityFresh = Test-FreshPassedGate $manifest.security "security" $nowUtc
    $securityPassed = $securityFresh -and (Test-AllTrue $manifest.security @(
        "malware_scan_passed",
        "signed",
        "signature_verified"
    ))
    Add-ResultGate "security" $securityPassed `
        "SIGNING_AND_MALWARE_PASSED" "SIGNING_AND_MALWARE_REQUIRED"

    $artifactPassed = $true
    $artifactNames = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    $hasMsi = $false
    $hasCabinet = $false
    $hasManifest = $false
    try {
        if (@($manifest.artifacts).Count -lt 3 -or @($manifest.artifacts).Count -gt 128) {
            throw "release artifact inventory is incomplete"
        }
        foreach ($artifact in @($manifest.artifacts)) {
            Assert-ExactProperties $artifact @("path", "bytes", "sha256") "artifact"
            $relative = [string]$artifact.path
            if (-not $artifactNames.Add($relative)) {
                throw "release artifact inventory contains duplicate paths"
            }
            $path = Resolve-RelativeArtifact $bundleRoot $relative "artifact path"
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                throw "release artifact is missing"
            }
            $file = Get-Item -LiteralPath $path
            if ([long]$artifact.bytes -le 0 -or [long]$artifact.bytes -ne $file.Length) {
                throw "release artifact size does not match"
            }
            $expectedHash = [string]$artifact.sha256
            if ($expectedHash -notmatch "^[0-9a-f]{64}$") {
                throw "release artifact hash is invalid"
            }
            $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
            if ($actualHash -ne $expectedHash) {
                throw "release artifact digest does not match"
            }
            $extension = [System.IO.Path]::GetExtension($relative)
            if ($extension -ieq ".msi") { $hasMsi = $true }
            if ($extension -ieq ".cab") { $hasCabinet = $true }
            if ($relative -eq [string]$manifest.installer.manifest_relative_path) {
                $hasManifest = $true
            }
        }
        if (-not $hasMsi -or -not $hasCabinet -or -not $hasManifest) {
            throw "release artifacts must include MSI, cabinet, and manifest"
        }
    }
    catch {
        $artifactPassed = $false
    }
    Add-ResultGate "artifacts" $artifactPassed "ARTIFACTS_VERIFIED" "ARTIFACTS_INVALID"
}

$allPassed = $null -ne $manifest -and @(
    $gates | Where-Object { $_.status -ne "passed" }
).Count -eq 0
$reportStatus = "blocked"
if ($allPassed) { $reportStatus = "passed" }
$reportVersion = $null
if ($null -ne $manifest) { $reportVersion = [string]$manifest.version }
$report = [ordered]@{
    schema_version = 1
    status = $reportStatus
    product = "Fairy"
    version = $reportVersion
    generated_at_utc = [datetime]::UtcNow.ToString("o")
    evidence_file = "realtime-beta-release-evidence.json"
    gates = @($gates)
}
$temporaryOutput = "$resolvedOutput.tmp-$PID"
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temporaryOutput -Encoding utf8
Move-Item -LiteralPath $temporaryOutput -Destination $resolvedOutput

if ($allPassed) {
    Write-Host "Fairy Realtime Companion Beta release gate passed: $resolvedOutput"
    exit 0
}
Write-Host "Fairy Realtime Companion Beta release gate blocked: $resolvedOutput"
exit 1
