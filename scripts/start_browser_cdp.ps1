param(
  [int]$Port = 9778,
  [string]$BrowserExe = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
)

if (-not (Test-Path $BrowserExe)) {
  throw "Browser executable not found: $BrowserExe"
}

$profilePath = Join-Path $env:TEMP "fairy_chrome_cdp_$Port"
New-Item -ItemType Directory -Force -Path $profilePath | Out-Null

Write-Host "Starting browser with remote debugging on port $Port"
Write-Host "Executable: $BrowserExe"
Write-Host "Profile: $profilePath"

& $BrowserExe --headless=new --disable-gpu --no-sandbox --remote-debugging-port=$Port --user-data-dir="$profilePath" about:blank
