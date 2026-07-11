$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($null -ne $uvCommand) {
    $uv = $uvCommand.Source
} else {
    $python = (& python -c "import sys; print(sys.executable)").Trim()
    $candidates = @(
        $env:UV,
        (Join-Path (Split-Path -Parent $python) "uv.exe"),
        (Join-Path (Split-Path -Parent $python) "Scripts\uv.exe"),
        (Join-Path $env:SystemDrive "Python313\Scripts\uv.exe"),
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe")
    )
    $uv = $candidates | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_) -and (Test-Path -LiteralPath $_)
    } | Select-Object -First 1
}

$exportScript = Join-Path $root "cloud\scripts\export_openapi.py"
$openApiPath = Join-Path $root "contracts\openapi.json"
if (-not [string]::IsNullOrWhiteSpace($uv)) {
    & $uv run --project (Join-Path $root "cloud") python $exportScript $openApiPath
} else {
    $cloudPython = Join-Path $root "cloud\.venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $cloudPython)) {
        throw "uv was not found and the locked Cloud virtual environment is unavailable"
    }
    Write-Host "uv not found; using the existing locked Cloud virtual environment"
    & $cloudPython $exportScript $openApiPath
}
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

npm --prefix (Join-Path $root "tools\contracts") run generate
exit $LASTEXITCODE
