[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Executable
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Start-Runtime {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.Arguments = "--stdio --media-pipe fairy-contract-integration"
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    if (-not $process.Start()) {
        throw "Unable to start contract runtime."
    }
    return $process
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

$process = Start-Runtime
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
        type = "ping"
        session_id = "session-integration"
        segment_id = "segment-integration"
        context_epoch = 1
        sequence = 2
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
        sequence = 3
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
    $process.Dispose()
}

$rejectingProcess = Start-Runtime
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
    $rejectingProcess.Dispose()
}

Write-Output "control integration tests passed"
