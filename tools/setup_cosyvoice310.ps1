param(
    [string]$PythonDir = "D:\桌面\~\deskllmchat\tools\python310",
    [string]$InstallerUrl = "https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$requirements = Join-Path $root "cosyvoice310-requirements.txt"
$installer = Join-Path $root "python-3.10.11-amd64.exe"

if (-not (Test-Path $installer)) {
    Invoke-WebRequest -Uri $InstallerUrl -OutFile $installer
}

if (-not (Test-Path (Join-Path $PythonDir "python.exe"))) {
    Start-Process -FilePath $installer -ArgumentList @(
        "/quiet",
        "InstallAllUsers=0",
        "PrependPath=0",
        "Include_test=0",
        "SimpleInstall=1",
        "TargetDir=$PythonDir"
    ) -Wait -NoNewWindow
}

$python = Join-Path $PythonDir "python.exe"
if (-not (Test-Path $python)) {
    throw "Python 3.10 installation failed: $python not found"
}

& $python -m pip install --upgrade pip
& $python -m pip install -r $requirements
