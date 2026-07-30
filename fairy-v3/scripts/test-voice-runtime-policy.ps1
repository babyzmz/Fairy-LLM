[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "voice-runtime-policy.ps1")

function New-ValidVoiceRuntimeReport {
    return [pscustomobject]@{
        python = "3.10.11"
        torch = "2.7.0+cu128"
        torchaudio = "2.7.0+cu128"
        cuda_available = $true
        device_name = "Fixture GPU"
        tensorrt = "10.13.3.9"
        onnxruntime_import = "1.22.0"
        onnxruntime_providers = @(
            "TensorrtExecutionProvider",
            "CUDAExecutionProvider",
            "CPUExecutionProvider"
        )
        onnxruntime_cpu_distribution = $null
        onnxruntime_gpu_distribution = "1.22.0"
    }
}

[void](Assert-FairyVoiceRuntimeReport -Report (New-ValidVoiceRuntimeReport))

$conflict = New-ValidVoiceRuntimeReport
$conflict.onnxruntime_cpu_distribution = "1.23.2"
try {
    [void](Assert-FairyVoiceRuntimeReport -Report $conflict)
    throw "CPU/GPU ONNX package conflict was accepted"
}
catch {
    if ($_.Exception.Message -ne "VOICE_ONNX_PACKAGE_CONFLICT") {
        throw
    }
}

$cpuOnly = New-ValidVoiceRuntimeReport
$cpuOnly.onnxruntime_providers = @("CPUExecutionProvider")
try {
    [void](Assert-FairyVoiceRuntimeReport -Report $cpuOnly)
    throw "CPU-only ONNX providers were accepted"
}
catch {
    if ($_.Exception.Message -ne "VOICE_ONNX_CUDA_PROVIDER_UNAVAILABLE") {
        throw
    }
}

Write-Host "Voice runtime policy passed."

