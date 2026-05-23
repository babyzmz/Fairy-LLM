[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$BackendPort = 8000,
    [int]$BackendReadyTimeoutSec = 30,
    [string]$SessionName = "fairy_desktop_dev",
    [bool]$StartBrowserCdp = $true,
    [int]$BrowserPort = 9778,
    [int]$BrowserReadyTimeoutSec = 20,
    [string]$BrowserExe = "",
    [bool]$BrowserHeadless = $true
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$desktopRoot = Join-Path $repoRoot "fairy-desktop"
$runtimeDir = Join-Path $repoRoot "data\runtime"
$stateFile = Join-Path $runtimeDir "$SessionName.json"
$backendHealthUrl = "http://$HostAddress`:$BackendPort/health"
$backendApiModule = "app.api.main:app"
$backendArgs = "-m uvicorn $backendApiModule --host $HostAddress --port $BackendPort"
$browserDebugUrl = "http://127.0.0.1`:$BrowserPort/json/version"
$script:backendProcess = $null
$script:tauriProcess = $null
$script:browserProcess = $null

function Write-Status {
    param([string]$Message)
    Write-Host "[Fairy Dev] $Message"
}

function Resolve-PythonExe {
    $candidates = @(
        (Join-Path $repoRoot "cosyvoice_env\Scripts\python.exe"),
        "python"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -eq "python") {
            try {
                $cmd = Get-Command python -ErrorAction Stop
                return $cmd.Source
            }
            catch {
                continue
            }
        }
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    throw "Unable to locate Python executable."
}

function Resolve-BrowserExe {
    if ($BrowserExe -and (Test-Path $BrowserExe)) {
        return $BrowserExe
    }
    $candidates = @(
        $env:FAIRY_BROWSER_EXECUTABLE_PATH,
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    ) | Where-Object { $_ }
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    throw "Unable to locate browser executable. Set -BrowserExe or FAIRY_BROWSER_EXECUTABLE_PATH."
}

function Test-BackendReady {
    try {
        $response = Invoke-RestMethod -Uri $backendHealthUrl -TimeoutSec 3 -Method Get
        return ($response.status -eq "ok")
    }
    catch {
        return $false
    }
}

function Test-BrowserReady {
    try {
        $response = Invoke-RestMethod -Uri $browserDebugUrl -TimeoutSec 3 -Method Get
        return [bool]($response.Browser -or $response.'Protocol-Version')
    }
    catch {
        return $false
    }
}

function Wait-BackendReady {
    param([int]$TimeoutSec)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-BackendReady) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Wait-BrowserReady {
    param([int]$TimeoutSec)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-BrowserReady) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

if (-not (Test-Path $desktopRoot)) {
    throw "Missing fairy-desktop directory: $desktopRoot"
}

if (-not (Test-Path (Join-Path $desktopRoot "node_modules"))) {
    throw "Missing fairy-desktop\\node_modules. Run npm install in $desktopRoot first."
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

$pythonExe = Resolve-PythonExe
$browserExecutable = $null
if ($StartBrowserCdp) {
    $browserExecutable = Resolve-BrowserExe
}

$jobTypeSource = @"
using System;
using System.Runtime.InteropServices;

public static class FairyJobObject {
    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
        public Int64 PerProcessUserTimeLimit;
        public Int64 PerJobUserTimeLimit;
        public UInt32 LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public UInt32 ActiveProcessLimit;
        public Int64 Affinity;
        public UInt32 PriorityClass;
        public UInt32 SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct IO_COUNTERS {
        public UInt64 ReadOperationCount;
        public UInt64 WriteOperationCount;
        public UInt64 OtherOperationCount;
        public UInt64 ReadTransferCount;
        public UInt64 WriteTransferCount;
        public UInt64 OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    private const int JobObjectExtendedLimitInformation = 9;
    private const UInt32 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string lpName);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr hJob, int infoType, IntPtr lpJobObjectInfo, uint cbJobObjectInfoLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    public static IntPtr CreateKillOnClose(string name) {
        IntPtr handle = CreateJobObject(IntPtr.Zero, name);
        if (handle == IntPtr.Zero) {
            throw new InvalidOperationException("CreateJobObject failed.");
        }
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        int length = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
        IntPtr infoPtr = Marshal.AllocHGlobal(length);
        try {
            Marshal.StructureToPtr(info, infoPtr, false);
            if (!SetInformationJobObject(handle, JobObjectExtendedLimitInformation, infoPtr, (uint)length)) {
                throw new InvalidOperationException("SetInformationJobObject failed.");
            }
        } finally {
            Marshal.FreeHGlobal(infoPtr);
        }
        return handle;
    }

    public static void Assign(IntPtr jobHandle, IntPtr processHandle) {
        if (!AssignProcessToJobObject(jobHandle, processHandle)) {
            throw new InvalidOperationException("AssignProcessToJobObject failed.");
        }
    }
}
"@

if (-not ("FairyJobObject" -as [type])) {
    Add-Type -TypeDefinition $jobTypeSource
}

$jobName = "FairyDesktopDev-$PID"
$jobHandle = [FairyJobObject]::CreateKillOnClose($jobName)
$childProcesses = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Add-ProcessToJob {
    param([System.Diagnostics.Process]$Process)
    [FairyJobObject]::Assign($jobHandle, $Process.Handle)
    [void]$childProcesses.Add($Process)
}

function Write-StateFile {
    $state = @{
        session_name = $SessionName
        started_at = (Get-Date).ToString("o")
        launcher_pid = $PID
        backend = @{
            pid = if ($script:backendProcess) { $script:backendProcess.Id } else { 0 }
            url = "http://$HostAddress`:$BackendPort"
            health_url = $backendHealthUrl
        }
        tauri = @{
            pid = if ($script:tauriProcess) { $script:tauriProcess.Id } else { 0 }
        }
        browser = @{
            pid = if ($script:browserProcess) { $script:browserProcess.Id } else { 0 }
            url = "http://127.0.0.1`:$BrowserPort"
            devtools_url = $browserDebugUrl
            executable = if ($script:browserProcess) { $script:browserProcess.Path } else { "" }
        }
    }
    $state | ConvertTo-Json -Depth 4 | Set-Content -Path $stateFile -Encoding UTF8
}

function Stop-Children {
    foreach ($proc in $childProcesses) {
        if ($null -ne $proc -and -not $proc.HasExited) {
            try {
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
            catch {
            }
        }
    }
}

$null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action {
    if (Test-Path $using:stateFile) {
        Remove-Item -Path $using:stateFile -Force -ErrorAction SilentlyContinue
    }
} | Out-Null

try {
    if (Test-BackendReady) {
        throw "Backend already running at $backendHealthUrl. Stop the existing backend first, or use the existing session."
    }
    if ($StartBrowserCdp -and (Test-BrowserReady)) {
        throw "Browser CDP already running at $browserDebugUrl. Stop the existing browser session first, or use the existing session."
    }

    if ($StartBrowserCdp) {
        $browserProfilePath = Join-Path $runtimeDir "browser_cdp_$SessionName"
        New-Item -ItemType Directory -Path $browserProfilePath -Force | Out-Null
        $browserArgs = @()
        if ($BrowserHeadless) {
            $browserArgs += "--headless=new"
        }
        $browserArgs += @(
            "--disable-gpu",
            "--no-sandbox",
            "--remote-debugging-port=$BrowserPort",
            "--user-data-dir=$browserProfilePath",
            "about:blank"
        )
        Write-Status "Starting browser CDP on $browserDebugUrl"
        $script:browserProcess = Start-Process -FilePath $browserExecutable -ArgumentList $browserArgs -WorkingDirectory $repoRoot -PassThru
        Add-ProcessToJob -Process $script:browserProcess
        if (-not (Wait-BrowserReady -TimeoutSec $BrowserReadyTimeoutSec)) {
            Write-Warning "Browser CDP failed to become ready within $BrowserReadyTimeoutSec seconds. Continuing without browser automation."
            try {
                Stop-Process -Id $script:browserProcess.Id -Force -ErrorAction SilentlyContinue
            }
            catch {
            }
            [void]$childProcesses.Remove($script:browserProcess)
            $script:browserProcess = $null
        }
        else {
            Write-Status "Browser CDP ready"
        }
    }

    Write-Status "Starting backend on $backendHealthUrl"
    $script:backendProcess = Start-Process -FilePath $pythonExe -ArgumentList $backendArgs -WorkingDirectory $repoRoot -PassThru
    Add-ProcessToJob -Process $script:backendProcess

    if (-not (Wait-BackendReady -TimeoutSec $BackendReadyTimeoutSec)) {
        throw "Backend failed to become ready within $BackendReadyTimeoutSec seconds."
    }
    Write-Status "Backend ready"

    Write-Status "Starting Tauri dev shell"
    $script:tauriProcess = Start-Process -FilePath "npm.cmd" -ArgumentList "run tauri:dev" -WorkingDirectory $desktopRoot -PassThru
    Add-ProcessToJob -Process $script:tauriProcess

    Write-StateFile

    Write-Status "Session file: $stateFile"
    Write-Status "Press Ctrl+C to stop browser, backend, and Tauri together."

    while ($true) {
        Start-Sleep -Seconds 1
        foreach ($proc in $childProcesses) {
            if ($proc.HasExited) {
                if ($script:tauriProcess -and $proc.Id -eq $script:tauriProcess.Id) {
                    Write-Warning "Tauri launcher process exited; leaving child shell processes managed by job object."
                    [void]$childProcesses.Remove($proc)
                    $script:tauriProcess = $null
                    break
                }
                throw "Process exited early: PID=$($proc.Id)"
            }
        }
    }
}
finally {
    Write-Status "Stopping child processes"
    Stop-Children
    if (Test-Path $stateFile) {
        Remove-Item -Path $stateFile -Force -ErrorAction SilentlyContinue
    }
}
