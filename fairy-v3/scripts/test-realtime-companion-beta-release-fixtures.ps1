[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$gate = Join-Path $root "scripts\test-realtime-companion-beta-release.ps1"
$scratch = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("fairy-release-gate-fixtures-" + [guid]::NewGuid().ToString("N"))
$evidenceRoot = Join-Path $scratch "evidence"
$bundleRoot = Join-Path $scratch "bundle"
New-Item -ItemType Directory -Force -Path $evidenceRoot, $bundleRoot | Out-Null

function Get-Artifact([string]$RelativePath) {
    $path = Join-Path $bundleRoot $RelativePath
    $file = Get-Item -LiteralPath $path
    return [ordered]@{
        path = $RelativePath.Replace("\", "/")
        bytes = $file.Length
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    }
}

function New-PassingEvidence {
    $observed = [datetime]::UtcNow.ToString("o")
    return [ordered]@{
        schema_version = 1
        product = "Fairy"
        version = "0.2.0"
        phase_acceptance = [ordered]@{
            status = "passed"
            observed_at = $observed
            phase_0_through_7 = $true
        }
        regression = [ordered]@{
            status = "passed"
            observed_at = $observed
            core = $true
            rust = $true
            typescript = $true
            vitest = $true
            playwright = $true
        }
        soak = [ordered]@{
            status = "passed"
            observed_at = $observed
            observed_duration_seconds = 14400
            context_rotations = 20
            pause_resume_cycles = 10
            window_switches = 5
            backend = "local_mini_cpm_o45"
            terminal_session_status = "completed"
            digest_count = 1
            sidecar_recovery = $true
            second_failure_quarantined = $true
            process_cleanup_confirmed = $true
        }
        native = [ordered]@{
            status = "passed"
            observed_at = $observed
            proposal_authority = $true
            resource_projection = $true
            crash_recovery = $true
            quarantine = $true
            companion_reload = $true
            no_worker_preheat = $true
        }
        performance = [ordered]@{
            status = "passed"
            observed_at = $observed
            model_ready_progress = $true
            visual_p95_ms = 1500
            speech_to_text_p95_ms = 1500
            voice_start_p95_ms = 2000
            barge_in_p95_ms = 120
            queues_bounded = $true
            latest_frame_only = $true
            median_gpu_frame_time_impact_percent = 10
            game_one_percent_low_impact_percent = 15
            no_game_or_desktop_crash = $true
        }
        privacy = [ordered]@{
            status = "passed"
            observed_at = $observed
            selected_window_only = $true
            sensitive_window_pause = $true
            cloud_scope_disclosed = $true
            local_scope_disclosed = $true
            pause_stops_frames = $true
            no_raw_media_on_disk = $true
            no_content_in_logs = $true
            telemetry_default_off = $true
        }
        installer = [ordered]@{
            status = "passed"
            observed_at = $observed
            install = $true
            start = $true
            upgrade = $true
            uninstall = $true
            user_data_preserved = $true
            cold_start_no_preheat = $true
            manifest_relative_path = "release-manifest.json"
        }
        security = [ordered]@{
            status = "passed"
            observed_at = $observed
            malware_scan_passed = $true
            signed = $true
            signature_verified = $true
        }
        artifacts = @(
            (Get-Artifact "Fairy_0.2.0_x64_en-US.msi"),
            (Get-Artifact "cab1.cab"),
            (Get-Artifact "release-manifest.json")
        )
    }
}

function Write-Evidence($Evidence) {
    $path = Join-Path $evidenceRoot "realtime-beta-release-evidence.json"
    $Evidence | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $path -Encoding utf8
}

function Invoke-Gate([string]$Name, $Evidence, [int]$ExpectedExitCode) {
    Write-Evidence $Evidence
    $report = Join-Path $scratch "${Name}.json"
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $commandOutput = @(
            & powershell -NoProfile -ExecutionPolicy Bypass -File $gate `
                -EvidenceDirectory $evidenceRoot `
                -BundleDirectory $bundleRoot `
                -OutputPath $report `
                -MaxEvidenceAgeDays 30 2>&1
        )
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne $ExpectedExitCode) {
        throw "$Name returned $exitCode; expected $ExpectedExitCode; output: $($commandOutput -join ' ')"
    }
    if (-not (Test-Path -LiteralPath $report -PathType Leaf)) {
        throw "$Name produced no report"
    }
    return Get-Content -Raw -LiteralPath $report | ConvertFrom-Json
}

function Invoke-RawGate(
    [string]$Name,
    [int]$ExpectedExitCode,
    [bool]$ExpectReport = $true
) {
    $report = Join-Path $scratch "${Name}.json"
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $commandOutput = @(
            & powershell -NoProfile -ExecutionPolicy Bypass -File $gate `
                -EvidenceDirectory $evidenceRoot `
                -BundleDirectory $bundleRoot `
                -OutputPath $report `
                -MaxEvidenceAgeDays 30 2>&1
        )
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne $ExpectedExitCode) {
        throw "$Name returned $exitCode; expected $ExpectedExitCode; output: $($commandOutput -join ' ')"
    }
    $reportExists = Test-Path -LiteralPath $report -PathType Leaf
    if ($reportExists -ne $ExpectReport) {
        throw "$Name report existence did not match the expected outcome"
    }
    if ($reportExists) {
        return Get-Content -Raw -LiteralPath $report | ConvertFrom-Json
    }
    return $null
}

try {
    Set-Content -LiteralPath (Join-Path $bundleRoot "Fairy_0.2.0_x64_en-US.msi") -Value "msi"
    Set-Content -LiteralPath (Join-Path $bundleRoot "cab1.cab") -Value "cabinet"
    Set-Content -LiteralPath (Join-Path $bundleRoot "release-manifest.json") -Value "{}"

    $passing = Invoke-Gate "passing" (New-PassingEvidence) 0
    if ([string]$passing.status -ne "passed") {
        throw "passing fixture did not pass"
    }

    $duration = New-PassingEvidence
    $duration.soak.observed_duration_seconds = 14399
    [void](Invoke-Gate "duration" $duration 1)

    $rotation = New-PassingEvidence
    $rotation.soak.context_rotations = 19
    [void](Invoke-Gate "rotation" $rotation 1)

    $pauseResume = New-PassingEvidence
    $pauseResume.soak.pause_resume_cycles = 9
    [void](Invoke-Gate "pause-resume" $pauseResume 1)

    $windowSwitch = New-PassingEvidence
    $windowSwitch.soak.window_switches = 4
    [void](Invoke-Gate "window-switch" $windowSwitch 1)

    $visual = New-PassingEvidence
    $visual.performance.visual_p95_ms = 1501
    [void](Invoke-Gate "visual-latency" $visual 1)

    $speechText = New-PassingEvidence
    $speechText.performance.speech_to_text_p95_ms = 1501
    [void](Invoke-Gate "speech-text-latency" $speechText 1)

    $voiceStart = New-PassingEvidence
    $voiceStart.performance.voice_start_p95_ms = 2001
    [void](Invoke-Gate "voice-start-latency" $voiceStart 1)

    $bargeIn = New-PassingEvidence
    $bargeIn.performance.barge_in_p95_ms = 121
    [void](Invoke-Gate "barge-in" $bargeIn 1)

    $frameTime = New-PassingEvidence
    $frameTime.performance.median_gpu_frame_time_impact_percent = 10.01
    [void](Invoke-Gate "frame-time" $frameTime 1)

    $onePercentLow = New-PassingEvidence
    $onePercentLow.performance.game_one_percent_low_impact_percent = 15.01
    [void](Invoke-Gate "one-percent-low" $onePercentLow 1)

    foreach ($domain in @(
        "phase_acceptance",
        "regression",
        "soak",
        "native",
        "performance",
        "privacy",
        "installer",
        "security"
    )) {
        $blocked = New-PassingEvidence
        $blocked.$domain.status = "blocked"
        [void](Invoke-Gate "blocked-$domain" $blocked 1)
    }

    $signing = New-PassingEvidence
    $signing.security.signed = $false
    [void](Invoke-Gate "signing" $signing 1)

    $forbidden = New-PassingEvidence
    $forbidden.privacy["transcript"] = "must never be accepted"
    [void](Invoke-Gate "forbidden-content" $forbidden 1)

    $digest = New-PassingEvidence
    $digest.artifacts[0].sha256 = ("0" * 64)
    [void](Invoke-Gate "digest" $digest 1)

    $escape = New-PassingEvidence
    $escape.artifacts[0].path = "../Fairy_0.2.0_x64_en-US.msi"
    [void](Invoke-Gate "path-escape" $escape 1)

    $absolute = New-PassingEvidence
    $absolute.artifacts[0].path = Join-Path $bundleRoot "Fairy_0.2.0_x64_en-US.msi"
    [void](Invoke-Gate "absolute-path" $absolute 1)

    $unexpected = New-PassingEvidence
    $unexpected.native["unexpected"] = $true
    [void](Invoke-Gate "unexpected-field" $unexpected 1)

    $stale = New-PassingEvidence
    $stale.native.observed_at = [datetime]::UtcNow.AddDays(-31).ToString("o")
    [void](Invoke-Gate "stale" $stale 1)

    $evidencePath = Join-Path $evidenceRoot "realtime-beta-release-evidence.json"
    Set-Content -LiteralPath $evidencePath -Value "{malformed"
    $malformed = Invoke-RawGate "malformed" 1
    if ([string]$malformed.status -ne "blocked") {
        throw "malformed evidence did not produce a blocked report"
    }

    Remove-Item -LiteralPath $evidencePath -Force
    $missing = Invoke-RawGate "missing" 1
    if ([string]$missing.status -ne "blocked") {
        throw "missing evidence did not produce a blocked report"
    }

    Write-Evidence (New-PassingEvidence)
    $overwritePath = Join-Path $scratch "overwrite.json"
    Set-Content -LiteralPath $overwritePath -Value "existing"
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & powershell -NoProfile -ExecutionPolicy Bypass -File $gate `
            -EvidenceDirectory $evidenceRoot `
            -BundleDirectory $bundleRoot `
            -OutputPath $overwritePath `
            -MaxEvidenceAgeDays 30 2>&1 | Out-Null
        $overwriteExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($overwriteExitCode -eq 0 -or
        (Get-Content -Raw -LiteralPath $overwritePath).Trim() -ne "existing") {
        throw "release gate did not refuse output overwrite"
    }

    Write-Host "Realtime Companion Beta release-gate fixtures passed."
}
finally {
    $resolvedScratch = [System.IO.Path]::GetFullPath($scratch)
    $temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if (-not $resolvedScratch.StartsWith(
        $temporaryRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing fixture cleanup outside the temporary directory"
    }
    Remove-Item -LiteralPath $resolvedScratch -Recurse -Force -ErrorAction SilentlyContinue
}
