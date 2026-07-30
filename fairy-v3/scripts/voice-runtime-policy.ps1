Set-StrictMode -Version Latest

function Assert-FairyVoiceRuntimeReport {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Report
    )

    if ($Report.python -ne "3.10.11") {
        throw "VOICE_PYTHON_VERSION_UNSUPPORTED"
    }
    if ($Report.torch -ne "2.7.0+cu128" -or $Report.torchaudio -ne "2.7.0+cu128") {
        throw "VOICE_TORCH_RUNTIME_MISMATCH"
    }
    if (-not $Report.cuda_available) {
        throw "VOICE_CUDA_UNAVAILABLE"
    }
    if ($Report.tensorrt -ne "10.13.3.9") {
        throw "VOICE_TRT_RUNTIME_MISMATCH"
    }
    if ($null -ne $Report.onnxruntime_cpu_distribution) {
        throw "VOICE_ONNX_PACKAGE_CONFLICT"
    }
    if ($Report.onnxruntime_gpu_distribution -ne "1.22.0") {
        throw "VOICE_ONNX_GPU_RUNTIME_MISMATCH"
    }
    if ($Report.onnxruntime_import -ne "1.22.0") {
        throw "VOICE_ONNX_IMPORT_MISMATCH"
    }
    if ("CUDAExecutionProvider" -notin @($Report.onnxruntime_providers)) {
        throw "VOICE_ONNX_CUDA_PROVIDER_UNAVAILABLE"
    }
    return $Report
}

function Get-FairyVoiceRuntimeReport {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath
    )

    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw "VOICE_PYTHON_RUNTIME_MISSING"
    }
    $probe = @'
import importlib.metadata as metadata
import json
import platform

import onnxruntime
import tensorrt
import torch
import torchaudio

def distribution_version(name):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None

print(json.dumps({
    'python': platform.python_version(),
    'torch': torch.__version__,
    'torchaudio': torchaudio.__version__,
    'cuda_available': bool(torch.cuda.is_available()),
    'device_name': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    'tensorrt': tensorrt.__version__,
    'onnxruntime_import': onnxruntime.__version__,
    'onnxruntime_providers': onnxruntime.get_available_providers(),
    'onnxruntime_cpu_distribution': distribution_version('onnxruntime'),
    'onnxruntime_gpu_distribution': distribution_version('onnxruntime-gpu'),
}, sort_keys=True))
'@
    $json = & $PythonPath -c $probe
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($json -join ""))) {
        throw "VOICE_RUNTIME_IMPORT_FAILED"
    }
    try {
        return ($json -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "VOICE_RUNTIME_REPORT_INVALID"
    }
}
