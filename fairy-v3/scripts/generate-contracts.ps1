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
    if ([string]::IsNullOrWhiteSpace($uv)) {
        throw "uv was not found on PATH or beside the active Python installation"
    }
}

& $uv run --project (Join-Path $root "cloud") python `
    (Join-Path $root "cloud\scripts\export_openapi.py") `
    (Join-Path $root "contracts\openapi.json")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

npm --prefix (Join-Path $root "tools\contracts") run generate
exit $LASTEXITCODE
