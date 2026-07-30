[CmdletBinding()]
param(
    [string]$PythonPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "voice-runtime-policy.ps1")

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $directory = [System.IO.DirectoryInfo]$root
    while ($null -ne $directory) {
        $candidate = Join-Path $directory.FullName "cosyvoice_env\Scripts\python.exe"
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $PythonPath = $candidate
            break
        }
        $directory = $directory.Parent
    }
}
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    throw "VOICE_PYTHON_RUNTIME_MISSING"
}

$report = Get-FairyVoiceRuntimeReport -PythonPath $PythonPath
[void](Assert-FairyVoiceRuntimeReport -Report $report)
$report | ConvertTo-Json -Depth 4

