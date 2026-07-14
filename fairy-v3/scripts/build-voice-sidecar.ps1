[CmdletBinding()]
param(
    [string]$TargetTriple = "x86_64-pc-windows-msvc"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$repository = [System.IO.DirectoryInfo]$root
while ($null -ne $repository -and -not (Test-Path -LiteralPath (Join-Path $repository.FullName "cosyvoice_env\Scripts\python.exe"))) {
    $repository = $repository.Parent
}
if ($null -eq $repository) {
    $gitCommonDir = (& git -C $root rev-parse --git-common-dir 2>$null)
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($gitCommonDir)) {
        $gitCommonDir = [System.IO.Path]::GetFullPath($gitCommonDir.Trim())
        $gitRepository = [System.IO.DirectoryInfo](Split-Path -Parent $gitCommonDir)
        if (Test-Path -LiteralPath (Join-Path $gitRepository.FullName "cosyvoice_env\Scripts\python.exe")) {
            $repository = $gitRepository
        }
    }
}
$runtimePython = if (-not [string]::IsNullOrWhiteSpace($env:FAIRY_COSYVOICE_PYTHON)) {
    $env:FAIRY_COSYVOICE_PYTHON
} elseif ($null -ne $repository) {
    Join-Path $repository.FullName "cosyvoice_env\Scripts\python.exe"
} else {
    $null
}
if ([string]::IsNullOrWhiteSpace($runtimePython) -or -not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
    throw "The locked CosyVoice CUDA Python runtime was not found. Set FAIRY_COSYVOICE_PYTHON."
}
$runtimeJson = & $runtimePython -c "import json, tensorrt, torch, torchaudio; print(json.dumps({'torch': torch.__version__, 'torchaudio': torchaudio.__version__, 'tensorrt': tensorrt.__version__}))"
if ($LASTEXITCODE -ne 0) { throw "The CosyVoice CUDA runtime is incomplete" }
$runtimeVersions = $runtimeJson | ConvertFrom-Json
if (
    $runtimeVersions.torch -ne "2.7.0+cu128" -or
    $runtimeVersions.torchaudio -ne "2.7.0+cu128" -or
    $runtimeVersions.tensorrt -ne "10.13.3.9"
) {
    throw "The CosyVoice CUDA runtime does not match the verified release baseline"
}

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$pyinstaller = & $runtimePython -c "import PyInstaller; print(PyInstaller.__version__)" 2>$null
$pyinstallerExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorActionPreference
if ($pyinstallerExitCode -ne 0 -or $pyinstaller -ne "6.16.0") {
    $uv = if (Test-Path -LiteralPath "C:\Python313\Scripts\uv.exe") { "C:\Python313\Scripts\uv.exe" } else { "uv" }
    & $uv pip install --python $runtimePython "pyinstaller==6.16.0"
    if ($LASTEXITCODE -ne 0) { throw "Unable to install the pinned voice sidecar build tool" }
}

$cosyvoiceRoot = if (-not [string]::IsNullOrWhiteSpace($env:FAIRY_COSYVOICE_ROOT)) {
    $env:FAIRY_COSYVOICE_ROOT
} elseif ($null -ne $repository) {
    Join-Path $repository.FullName "third_party\CosyVoice"
} else {
    $null
}
if ([string]::IsNullOrWhiteSpace($cosyvoiceRoot) -or -not (Test-Path -LiteralPath (Join-Path $cosyvoiceRoot "cosyvoice\cli\cosyvoice.py"))) {
    throw "The pinned official CosyVoice source was not found. Set FAIRY_COSYVOICE_ROOT."
}
$runtimeEnvironment = Split-Path -Parent (Split-Path -Parent $runtimePython)
$xTransformersRoot = Join-Path $runtimeEnvironment "Lib\site-packages\x_transformers"
if (-not (Test-Path -LiteralPath $xTransformersRoot -PathType Container)) {
    throw "The CosyVoice x-transformers runtime source was not found"
}

$outputRoot = Join-Path $root "desktop\src-tauri\runtime"
$destination = Join-Path $outputRoot "voice-worker"
$outputRootFull = [System.IO.Path]::GetFullPath($outputRoot).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
$destinationFull = [System.IO.Path]::GetFullPath($destination)
if (-not $destinationFull.StartsWith($outputRootFull + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Voice Worker destination escaped the Tauri runtime directory"
}
$scratch = Join-Path ([System.IO.Path]::GetTempPath()) "fairy-v3-voice-sidecar"
$dist = Join-Path $scratch "dist"
$work = Join-Path $scratch "work"
$spec = Join-Path $scratch "spec"
Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $dist, $work, $spec, $outputRoot | Out-Null

try {
    $pyinstallerEntry = "import sys; sys.setrecursionlimit(10000); from PyInstaller.__main__ import run; run()"
    & $runtimePython -c $pyinstallerEntry `
        --noconfirm `
        --clean `
        --noupx `
        --onedir `
        --name fairy-voice-worker `
        --distpath $dist `
        --workpath $work `
        --specpath $spec `
        --paths (Join-Path $root "voice-worker\src") `
        --paths $cosyvoiceRoot `
        --paths (Join-Path $cosyvoiceRoot "third_party\Matcha-TTS") `
        --add-data ((Join-Path $cosyvoiceRoot "cosyvoice") + ";cosyvoice") `
        --add-data ((Join-Path $cosyvoiceRoot "third_party\Matcha-TTS\matcha") + ";matcha") `
        --add-data ($xTransformersRoot + ";x_transformers") `
        --collect-data whisper `
        --hidden-import cosyvoice.cli.cosyvoice `
        --hidden-import cosyvoice.dataset.processor `
        --hidden-import cosyvoice.flow.DiT.dit `
        --hidden-import cosyvoice.flow.flow `
        --hidden-import cosyvoice.flow.flow_matching `
        --hidden-import cosyvoice.hifigan.discriminator `
        --hidden-import cosyvoice.hifigan.f0_predictor `
        --hidden-import cosyvoice.hifigan.generator `
        --hidden-import cosyvoice.hifigan.hifigan `
        --hidden-import cosyvoice.llm.llm `
        --hidden-import cosyvoice.tokenizer.tokenizer `
        --hidden-import cosyvoice.transformer.upsample_encoder `
        --hidden-import cosyvoice.utils.common `
        --hidden-import matcha.hifigan.models `
        --hidden-import matcha.utils.audio `
        --hidden-import conformer `
        --hidden-import einx `
        --hidden-import loguru `
        --hidden-import omegaconf `
        --hidden-import torch `
        --hidden-import torchaudio `
        --hidden-import tensorrt `
        --exclude-module modelscope `
        --exclude-module typeguard `
        --exclude-module x_transformers `
        (Join-Path $root "voice-worker\src\fairy_voice_worker\server.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

    $built = Join-Path $dist "fairy-voice-worker"
    if (-not (Test-Path -LiteralPath (Join-Path $built "fairy-voice-worker.exe") -PathType Leaf)) {
        throw "PyInstaller did not produce the Fairy Voice Worker runtime"
    }
    if (Test-Path -LiteralPath $destination) {
        Remove-Item -LiteralPath $destination -Recurse -Force
    }
    Copy-Item -LiteralPath $built -Destination $destination -Recurse
    $executable = Join-Path $destination "fairy-voice-worker.exe"
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $executable).Hash.ToLowerInvariant()
    Write-Host "Built $destination"
    Write-Host "SHA256 $hash"
}
finally {
    Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
}
