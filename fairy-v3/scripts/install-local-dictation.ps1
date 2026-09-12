param(
    [string]$Python = 'C:\Python313\python.exe',
    [string]$RuntimeRoot = (Join-Path $PSScriptRoot '..\.runtime\stt')
)
$ErrorActionPreference = 'Stop'
$resolvedRuntime = [IO.Path]::GetFullPath($RuntimeRoot)
if ([IO.Path]::GetPathRoot($resolvedRuntime) -ine 'D:\') {
    throw 'This installer is restricted to D: by the local dictation storage policy.'
}
New-Item -ItemType Directory -Path $resolvedRuntime -Force | Out-Null
$runtimePython = Join-Path $resolvedRuntime '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) {
    & $Python -m venv (Join-Path $resolvedRuntime '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated dictation environment.' }
}
& $runtimePython -m pip install --no-cache-dir -r (Join-Path $PSScriptRoot 'local-dictation-requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Local dictation dependency installation failed.' }
$previousHfHome = $env:HF_HOME
$previousImplicitToken = $env:HF_HUB_DISABLE_IMPLICIT_TOKEN
try {
    $env:HF_HOME = Join-Path $resolvedRuntime 'hf-cache'
    $env:HF_HUB_DISABLE_IMPLICIT_TOKEN = '1'
    $hub = Join-Path $resolvedRuntime '.venv\Scripts\hf.exe'
    $revision = '536b0662742c02347bc0e980a01041f333bce120'
    $model = Join-Path $resolvedRuntime 'model'
    & $hub download Systran/faster-whisper-small --revision $revision --local-dir $model --max-workers 2 --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Model download failed; rerun to resume.' }
    & $hub cache verify Systran/faster-whisper-small --revision $revision --local-dir $model
    if ($LASTEXITCODE -ne 0) { throw 'Model verification failed; do not use the partial runtime.' }
} finally {
    $env:HF_HOME = $previousHfHome
    $env:HF_HUB_DISABLE_IMPLICIT_TOKEN = $previousImplicitToken
}
Write-Output "Local dictation installed at $resolvedRuntime. Restart Fairy to refresh availability."
