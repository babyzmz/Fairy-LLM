[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "PRESENCE_PLATFORM_UNSUPPORTED: Windows is required"
}

$OperatingSystem = Get-CimInstance Win32_OperatingSystem
$WebViewRoot = "C:\Program Files (x86)\Microsoft\EdgeWebView\Application"
$WebViewVersion = Get-ChildItem -LiteralPath $WebViewRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object Name -Match '^\d' |
    ForEach-Object { [version]$_.Name } |
    Sort-Object -Descending |
    Select-Object -First 1
$GpuNames = @(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name)
$HardwareGpus = @($GpuNames | Where-Object { $_ -NotMatch 'Virtual Display' })

if ([int]$OperatingSystem.BuildNumber -lt 22000) {
    throw "PRESENCE_PLATFORM_UNSUPPORTED: Windows 11 build 22000 or newer is required"
}
if ($null -eq $WebViewVersion) {
    throw "WEBVIEW2_UNAVAILABLE: Microsoft Edge WebView2 Runtime is required"
}
if ($HardwareGpus.Count -eq 0) {
    throw "GPU_UNAVAILABLE: no hardware display adapter was detected"
}

[PSCustomObject]@{
    os = $OperatingSystem.Caption
    build = [int]$OperatingSystem.BuildNumber
    webview2 = $WebViewVersion.ToString()
    hardware_gpus = $HardwareGpus
    webgl2_runtime_probe = "required"
} | ConvertTo-Json -Depth 3
