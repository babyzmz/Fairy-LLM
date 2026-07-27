[CmdletBinding()]
param(
    [string]$PatchedSource
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-Matches {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$Pattern,
        [Parameter(Mandatory = $true)][string]$Description
    )
    if ($Text -notmatch $Pattern) {
        throw "Backend boundary is missing $Description."
    }
}

$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$adapter = Get-Content -LiteralPath (Join-Path $runtimeRoot "src\backend_upstream.cpp") -Raw -Encoding UTF8
if ($adapter -match "https?://|WinHttp|ShellExecute|\bsystem\s*\(|\bfopen\s*\(|\bofstream\b") {
    throw "The embedded upstream adapter contains a forbidden network, shell, or media-file output API."
}
Assert-Matches -Text $adapter -Pattern "params_\.offline\s*=\s*true" -Description "offline mode"
Assert-Matches -Text $adapter -Pattern "omni_init\([\s\S]*?false,[\s\S]*?true," -Description "TTS-disabled duplex initialization"
Assert-Matches -Text $adapter -Pattern "omni_set_output_policy\(context_, false, false, 8192\)" -Description "memory-only output policy"
Assert-Matches -Text $adapter -Pattern "params_\.tts_model\.clear\(\)" -Description "TTS model exclusion"

if ([string]::IsNullOrWhiteSpace($PatchedSource)) {
    $PatchedSource = Join-Path $runtimeRoot "_deps\llama.cpp-omni-patched"
}
$patchedRoot = [IO.Path]::GetFullPath($PatchedSource)
$allowedRoot = [IO.Path]::GetFullPath((Join-Path $runtimeRoot "_deps")).TrimEnd('\', '/')
if (-not $patchedRoot.StartsWith(
    "$allowedRoot$([IO.Path]::DirectorySeparatorChar)",
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "Patched source path escapes the runtime dependency root."
}
$header = Get-Content -LiteralPath (Join-Path $patchedRoot "tools\omni\omni.h") -Raw -Encoding UTF8
$source = Get-Content -LiteralPath (Join-Path $patchedRoot "tools\omni\omni.cpp") -Raw -Encoding UTF8
Assert-Matches -Text $header -Pattern "std::vector<uint8_t> aud_bytes" -Description "memory audio frame"
Assert-Matches -Text $header -Pattern "std::vector<uint8_t> img_bytes" -Description "memory image frame"
Assert-Matches -Text $source -Pattern "decision exceeded configured byte limit" -Description "bounded decision rejection"
Assert-Matches -Text $source -Pattern "memory-only mode rejects file paths" -Description "file-path rejection"

Write-Output "backend boundary tests passed"
