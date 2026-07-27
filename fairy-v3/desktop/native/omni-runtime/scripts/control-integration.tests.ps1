[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Executable
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Start-Runtime {
    $pipeName = "fairy-contract-$PID-$([Guid]::NewGuid().ToString('N'))"
    $server = [IO.Pipes.NamedPipeServerStream]::new(
        $pipeName,
        [IO.Pipes.PipeDirection]::Out,
        1,
        [IO.Pipes.PipeTransmissionMode]::Byte,
        [IO.Pipes.PipeOptions]::Asynchronous
    )
    $connection = $server.WaitForConnectionAsync()
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $runtimeRoot = Split-Path -Parent $PSScriptRoot
    $manifest = [IO.Path]::GetFullPath(
        (Join-Path $runtimeRoot "..\..\src-tauri\resources\omni\minicpm-o-4.5.json")
    )
    $modelRoot = [IO.Path]::GetFullPath((Join-Path $runtimeRoot "tests\fixtures\model-root"))
    $info.Arguments = "--stdio --media-pipe `"$pipeName`" --manifest `"$manifest`" --model-root `"$modelRoot`""
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    if (-not $process.Start()) {
        $server.Dispose()
        throw "Unable to start contract runtime."
    }
    if (-not $connection.Wait(5000)) {
        $process.Kill()
        $process.WaitForExit()
        $process.Dispose()
        $server.Dispose()
        throw "Runtime did not connect to its parent-owned media pipe."
    }
    $manifestIdentity = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
    return [pscustomobject]@{
        Process = $process
        Pipe = $server
        ManifestDigest = $manifestIdentity.manifest_digest
        ModelVersion = $manifestIdentity.version
    }
}

function Read-Exactly {
    param(
        [Parameter(Mandatory = $true)]$Stream,
        [Parameter(Mandatory = $true)][int]$Count
    )

    $buffer = New-Object byte[] $Count
    $offset = 0
    while ($offset -lt $Count) {
        $task = $Stream.ReadAsync($buffer, $offset, $Count - $offset)
        if (-not $task.Wait(5000)) {
            throw "Timed out reading runtime control output."
        }
        $read = $task.Result
        if ($read -eq 0) {
            throw "Runtime control output ended before a complete frame."
        }
        $offset += $read
    }
    return $buffer
}

function Write-Frame {
    param(
        [Parameter(Mandatory = $true)]$Stream,
        [Parameter(Mandatory = $true)]$Payload
    )

    $json = $Payload | ConvertTo-Json -Compress -Depth 10
    $body = [Text.Encoding]::UTF8.GetBytes($json)
    $prefix = [BitConverter]::GetBytes([uint32]$body.Length)
    $Stream.Write($prefix, 0, $prefix.Length)
    $Stream.Write($body, 0, $body.Length)
    $Stream.Flush()
}

function Read-Frame {
    param([Parameter(Mandatory = $true)]$Stream)

    $prefix = Read-Exactly -Stream $Stream -Count 4
    $length = [BitConverter]::ToUInt32($prefix, 0)
    if ($length -eq 0 -or $length -gt 262144) {
        throw "Runtime emitted an invalid control length."
    }
    $body = Read-Exactly -Stream $Stream -Count ([int]$length)
    return ([Text.Encoding]::UTF8.GetString($body) | ConvertFrom-Json)
}

function Assert-Equal {
    param($Actual, $Expected, [string]$Message)
    if ($Actual -ne $Expected) {
        throw "$Message Expected '$Expected', got '$Actual'."
    }
}

function Write-Microphone-Frame {
    param(
        [Parameter(Mandatory = $true)]$Stream,
        [uint64]$Epoch,
        [uint64]$Sequence,
        [uint64]$TimestampUs
    )

    $header = New-Object byte[] 36
    [Text.Encoding]::ASCII.GetBytes("FOMI").CopyTo($header, 0)
    [BitConverter]::GetBytes([uint16]1).CopyTo($header, 4)
    [BitConverter]::GetBytes([uint16]1).CopyTo($header, 6)
    [BitConverter]::GetBytes($Epoch).CopyTo($header, 8)
    [BitConverter]::GetBytes($Sequence).CopyTo($header, 16)
    [BitConverter]::GetBytes($TimestampUs).CopyTo($header, 24)
    [BitConverter]::GetBytes([uint32]640).CopyTo($header, 32)
    $payload = New-Object byte[] 640
    $Stream.Write($header, 0, $header.Length)
    $Stream.Write($payload, 0, $payload.Length)
    $Stream.Flush()
}

function Write-Oversized-Jpeg-Header {
    param([Parameter(Mandatory = $true)]$Stream)

    $header = New-Object byte[] 36
    [Text.Encoding]::ASCII.GetBytes("FOMI").CopyTo($header, 0)
    [BitConverter]::GetBytes([uint16]1).CopyTo($header, 4)
    [BitConverter]::GetBytes([uint16]3).CopyTo($header, 6)
    [BitConverter]::GetBytes([uint64]1).CopyTo($header, 8)
    [BitConverter]::GetBytes([uint64]1).CopyTo($header, 16)
    [BitConverter]::GetBytes([uint64]20000).CopyTo($header, 24)
    [BitConverter]::GetBytes([uint32](8 * 1024 * 1024 + 1)).CopyTo($header, 32)
    $Stream.Write($header, 0, $header.Length)
    $Stream.Flush()
}

$runtime = Start-Runtime
$process = $runtime.Process
try {
    $input = $process.StandardInput.BaseStream
    $output = $process.StandardOutput.BaseStream
    Write-Frame -Stream $input -Payload @{
        type = "hello"
        protocol_version = 1
        session_id = "session-integration"
        segment_id = "segment-integration"
        context_epoch = 1
        sequence = 1
    }
    $ready = Read-Frame -Stream $output
    Assert-Equal $ready.type "ready" "Runtime did not emit ready."
    Assert-Equal $ready.backend_ready $false "Contract runtime claimed backend readiness."

    Write-Frame -Stream $input -Payload @{
        type = "load"
        session_id = "session-integration"
        segment_id = "segment-integration"
        context_epoch = 1
        sequence = 2
        manifest_digest = $runtime.ManifestDigest
        model_version = $runtime.ModelVersion
        system_instruction = "You are Fairy."
    }
    Assert-Equal (Read-Frame -Stream $output).type "load_progress" "Load progress missing."
    Assert-Equal (Read-Frame -Stream $output).type "model_ready" "Model state missing."

    Write-Frame -Stream $input -Payload @{
        type = "context_begin"
        session_id = "session-integration"
        segment_id = "segment-integration"
        context_epoch = 1
        sequence = 3
        context_kind = "duplex_conversation"
        token_budget = 4096
        audio_budget_ms = 60000
        frame_budget = 120
        video_width = 0
        video_height = 0
    }
    Assert-Equal (Read-Frame -Stream $output).type "context_ready" "Context state missing."

    Write-Microphone-Frame -Stream $runtime.Pipe -Epoch 1 -Sequence 1 -TimestampUs 20000
    Write-Frame -Stream $input -Payload @{
        type = "media_commit"
        session_id = "session-integration"
        segment_id = "segment-integration"
        context_epoch = 1
        sequence = 4
        media_sequence = 1
    }
    $decision = Read-Frame -Stream $output
    Assert-Equal $decision.type "decision" "Contract decision missing."
    Assert-Equal $decision.decision "listen" "Contract media path did not stay fail-closed."

    Write-Frame -Stream $input -Payload @{
        type = "ping"
        session_id = "session-integration"
        segment_id = "segment-integration"
        context_epoch = 1
        sequence = 5
        nonce = "roundtrip"
    }
    $pong = Read-Frame -Stream $output
    Assert-Equal $pong.type "pong" "Runtime did not emit pong."
    Assert-Equal $pong.nonce "roundtrip" "Runtime changed the ping nonce."

    Write-Frame -Stream $input -Payload @{
        type = "stop"
        session_id = "session-integration"
        segment_id = "segment-integration"
        context_epoch = 1
        sequence = 6
    }
    $stopped = Read-Frame -Stream $output
    Assert-Equal $stopped.type "stopped" "Runtime did not emit stopped."
    $process.StandardInput.Close()
    if (-not $process.WaitForExit(5000) -or $process.ExitCode -ne 0) {
        throw "Runtime did not exit cleanly after stop."
    }
} finally {
    if (-not $process.HasExited) {
        $process.Kill()
        $process.WaitForExit()
    }
    $runtime.Pipe.Dispose()
    $process.Dispose()
}

$rejectingRuntime = Start-Runtime
$rejectingProcess = $rejectingRuntime.Process
try {
    Write-Frame -Stream $rejectingProcess.StandardInput.BaseStream -Payload @{
        type = "hello"
        protocol_version = 1
        session_id = "session-reject"
        segment_id = "segment-reject"
        context_epoch = 1
        sequence = 1
        unknown = "denied"
    }
    $rejectingProcess.StandardInput.Close()
    if (-not $rejectingProcess.WaitForExit(5000)) {
        throw "Runtime did not fail closed for an unknown field."
    }
    Assert-Equal $rejectingProcess.ExitCode 2 "Unknown field exit code mismatch."
    $diagnostic = $rejectingProcess.StandardError.ReadToEnd()
    if ($diagnostic.Length -eq 0 -or $diagnostic.Length -gt 600) {
        throw "Runtime diagnostic was missing or unbounded."
    }
} finally {
    if (-not $rejectingProcess.HasExited) {
        $rejectingProcess.Kill()
        $rejectingProcess.WaitForExit()
    }
    $rejectingRuntime.Pipe.Dispose()
    $rejectingProcess.Dispose()
}

$mediaRejectRuntime = Start-Runtime
$mediaRejectProcess = $mediaRejectRuntime.Process
try {
    $input = $mediaRejectProcess.StandardInput.BaseStream
    $output = $mediaRejectProcess.StandardOutput.BaseStream
    Write-Frame -Stream $input -Payload @{
        type = "hello"
        protocol_version = 1
        session_id = "session-media-reject"
        segment_id = "segment-media-reject"
        context_epoch = 1
        sequence = 1
    }
    Assert-Equal (Read-Frame -Stream $output).type "ready" "Media rejection runtime was not ready."
    Write-Oversized-Jpeg-Header -Stream $mediaRejectRuntime.Pipe
    $diagnostic = $null
    for ($sequence = 2; $sequence -le 11 -and $null -eq $diagnostic; $sequence++) {
        Write-Frame -Stream $input -Payload @{
            type = "ping"
            session_id = "session-media-reject"
            segment_id = "segment-media-reject"
            context_epoch = 1
            sequence = $sequence
            nonce = "detect-media-failure"
        }
        $response = Read-Frame -Stream $output
        if ($response.type -eq "diagnostic") {
            $diagnostic = $response
        } else {
            Assert-Equal $response.type "pong" "Unexpected response while awaiting media failure."
            Start-Sleep -Milliseconds 20
        }
    }
    if ($null -eq $diagnostic) {
        throw "Malformed media was not rejected before payload allocation."
    }
    Assert-Equal $diagnostic.type "diagnostic" "Malformed media did not emit a diagnostic."
    Assert-Equal $diagnostic.code "media_frame_rejected" "Malformed media diagnostic changed."
    if (-not $mediaRejectProcess.WaitForExit(5000)) {
        throw "Runtime did not fail closed after malformed media."
    }
    Assert-Equal $mediaRejectProcess.ExitCode 2 "Malformed media exit code mismatch."
} finally {
    if (-not $mediaRejectProcess.HasExited) {
        $mediaRejectProcess.Kill()
        $mediaRejectProcess.WaitForExit()
    }
    $mediaRejectRuntime.Pipe.Dispose()
    $mediaRejectProcess.Dispose()
}

Write-Output "control integration tests passed"
