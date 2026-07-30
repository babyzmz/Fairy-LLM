[CmdletBinding()]
param(
    [string]$OpenRouterKeyFile,
    [switch]$Toggle
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repositoryRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd("\")
$launcherStatePath = Join-Path $repositoryRoot ".tmp\fairy-dev-launcher.json"
$launcherStopPath = Join-Path $repositoryRoot ".tmp\fairy-dev-launcher.stop"
$launcherInstanceId = $null
$launcherStartedAtUtc = $null
$launcherStartTicks = $null
$launcherOwnsState = $false

function Get-FairyStateValue {
    param(
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $property = $State.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Read-FairyLauncherState {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 |
            ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        return $null
    }
}

function Write-FairyLauncherState {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$InstanceId,
        [Parameter(Mandatory = $true)][long]$OwnerStartTicks,
        [Parameter(Mandatory = $true)][string]$StartedAtUtc,
        [AllowNull()][string]$PathAliasDrive
    )

    $directory = Split-Path -Parent $Path
    $null = New-Item -ItemType Directory -Path $directory -Force
    $temporaryPath = "$Path.$PID.tmp"
    $state = [ordered]@{
        schema_version = 1
        instance_id = $InstanceId
        owner_pid = $PID
        owner_start_time_utc_ticks = [string]$OwnerStartTicks
        repository_root = $repositoryRoot
        started_at_utc = $StartedAtUtc
        path_alias_drive = $PathAliasDrive
    }
    $json = $state | ConvertTo-Json
    [System.IO.File]::WriteAllText(
        $temporaryPath,
        $json,
        [System.Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
}

function Remove-FairyLauncherFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowNull()][string]$ExpectedInstanceId
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }
    if ($ExpectedInstanceId) {
        if ($Path -eq $launcherStatePath) {
            $state = Read-FairyLauncherState -Path $Path
            if ($null -eq $state -or
                (Get-FairyStateValue -State $state -Name "instance_id") -ne $ExpectedInstanceId) {
                return
            }
        }
        else {
            $content = Get-Content `
                -LiteralPath $Path `
                -Raw `
                -Encoding UTF8 `
                -ErrorAction SilentlyContinue
            if ($null -eq $content -or $content.Trim() -ne $ExpectedInstanceId) {
                return
            }
        }
    }
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
}

function Resolve-FairyLauncherProcess {
    param([Parameter(Mandatory = $true)]$State)

    $schemaVersion = [string](Get-FairyStateValue -State $State -Name "schema_version")
    $instanceId = [string](Get-FairyStateValue -State $State -Name "instance_id")
    if ($schemaVersion -ne "1" -or $instanceId -notmatch '^[0-9a-f]{32}$') {
        return $null
    }

    $stateRoot = [string](Get-FairyStateValue -State $State -Name "repository_root")
    if (-not [string]::Equals(
        $stateRoot.TrimEnd("\"),
        $repositoryRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        return $null
    }

    $ownerPid = 0
    if (-not [int]::TryParse(
        [string](Get-FairyStateValue -State $State -Name "owner_pid"),
        [ref]$ownerPid
    ) -or $ownerPid -eq $PID) {
        return $null
    }

    try {
        $process = Get-Process -Id $ownerPid -ErrorAction Stop
        if ($process.ProcessName -notin @("powershell", "pwsh")) {
            return $null
        }
        $expectedTicks = [string](
            Get-FairyStateValue -State $State -Name "owner_start_time_utc_ticks"
        )
        $actualTicks = [string]$process.StartTime.ToUniversalTime().Ticks
        if ($actualTicks -ne $expectedTicks) {
            return $null
        }
        return $process
    }
    catch {
        return $null
    }
}

function Request-FairyLauncherStop {
    param([Parameter(Mandatory = $true)][string]$InstanceId)

    $directory = Split-Path -Parent $launcherStopPath
    $null = New-Item -ItemType Directory -Path $directory -Force
    $temporaryPath = "$launcherStopPath.$PID.tmp"
    [System.IO.File]::WriteAllText(
        $temporaryPath,
        $InstanceId,
        [System.Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporaryPath -Destination $launcherStopPath -Force
}

function Test-FairyLauncherStopRequested {
    param([Parameter(Mandatory = $true)][string]$InstanceId)

    if (-not (Test-Path -LiteralPath $launcherStopPath -PathType Leaf)) {
        return $false
    }
    $requestedInstance = Get-Content `
        -LiteralPath $launcherStopPath `
        -Raw `
        -Encoding UTF8 `
        -ErrorAction SilentlyContinue
    return $null -ne $requestedInstance -and $requestedInstance.Trim() -eq $InstanceId
}

function Remove-FairyStalePathAlias {
    param([Parameter(Mandatory = $true)]$State)

    $drive = [string](Get-FairyStateValue -State $State -Name "path_alias_drive")
    if ($drive -notmatch '^[Q-Z]:$') {
        return
    }
    $mappings = @(& subst.exe 2>$null)
    if ($LASTEXITCODE -ne 0) {
        return
    }
    $prefix = "${drive}\: => "
    $mapping = $mappings | Where-Object {
        $_.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
    } | Select-Object -First 1
    if ($null -eq $mapping) {
        return
    }
    $target = $mapping.Substring($prefix.Length).Trim().TrimEnd("\")
    if ([string]::Equals(
        $target,
        $repositoryRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        & subst.exe $drive /D
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Could not remove stale desktop workspace alias $drive"
        }
    }
}

if ($Toggle) {
    $mutexHash = [System.Security.Cryptography.SHA256]::Create()
    try {
        $rootBytes = [System.Text.Encoding]::UTF8.GetBytes($repositoryRoot.ToLowerInvariant())
        $hashBytes = $mutexHash.ComputeHash($rootBytes)
        $hashText = -join ($hashBytes[0..7] | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $mutexHash.Dispose()
    }

    $launcherMutex = [System.Threading.Mutex]::new(
        $false,
        "Local\FairyV3DevLauncher_$hashText"
    )
    $mutexAcquired = $false
    $existingProcess = $null
    $existingState = $null
    $existingInstanceId = $null
    try {
        $mutexAcquired = $launcherMutex.WaitOne([TimeSpan]::FromSeconds(5))
        if (-not $mutexAcquired) {
            throw "Timed out waiting for the Fairy development launcher"
        }

        $existingState = Read-FairyLauncherState -Path $launcherStatePath
        if ($null -ne $existingState) {
            $existingProcess = Resolve-FairyLauncherProcess -State $existingState
        }

        if ($null -ne $existingProcess) {
            $existingInstanceId = [string](
                Get-FairyStateValue -State $existingState -Name "instance_id"
            )
            Request-FairyLauncherStop -InstanceId $existingInstanceId
        }
        else {
            if ($null -ne $existingState) {
                Remove-FairyStalePathAlias -State $existingState
            }
            Remove-Item -LiteralPath $launcherStatePath -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $launcherStopPath -Force -ErrorAction SilentlyContinue

            $launcherInstanceId = [Guid]::NewGuid().ToString("N")
            $launcherStartedAtUtc = [DateTime]::UtcNow.ToString("O")
            $launcherStartTicks = (Get-Process -Id $PID).StartTime.ToUniversalTime().Ticks
            Write-FairyLauncherState `
                -Path $launcherStatePath `
                -InstanceId $launcherInstanceId `
                -OwnerStartTicks $launcherStartTicks `
                -StartedAtUtc $launcherStartedAtUtc `
                -PathAliasDrive $null
            $launcherOwnsState = $true
        }
    }
    finally {
        if ($mutexAcquired) {
            $launcherMutex.ReleaseMutex()
        }
        $launcherMutex.Dispose()
    }

    if ($null -ne $existingProcess) {
        Write-Host "Stopping the Fairy development environment..."
        $exitedGracefully = $false
        try {
            $exitedGracefully = $existingProcess.WaitForExit(15000)
        }
        catch {
            $exitedGracefully = $true
        }
        if (-not $exitedGracefully) {
            Write-Warning "Graceful shutdown timed out; stopping the verified launcher process"
            Stop-Process -Id $existingProcess.Id -Force -ErrorAction SilentlyContinue
            try {
                $null = $existingProcess.WaitForExit(5000)
            }
            catch {
                # The process has already exited.
            }
            Remove-FairyStalePathAlias -State $existingState
        }
        Remove-FairyLauncherFile `
            -Path $launcherStatePath `
            -ExpectedInstanceId $existingInstanceId
        Remove-FairyLauncherFile `
            -Path $launcherStopPath `
            -ExpectedInstanceId $existingInstanceId
        $existingProcess.Dispose()
        Write-Host "Fairy development environment stopped."
        return
    }
}

try {

if ($null -eq ("FairyDevelopmentProcessJob" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;

public sealed class FairyDevelopmentProcessJob : IDisposable {
    const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
    IntPtr handle;

    [StructLayout(LayoutKind.Sequential)]
    struct BasicLimitInformation {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct IoCounters {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct ExtendedLimitInformation {
        public BasicLimitInformation BasicLimitInformation;
        public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetInformationJobObject(
        IntPtr job,
        int informationClass,
        IntPtr information,
        uint informationLength
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr handle);

    public FairyDevelopmentProcessJob() {
        handle = CreateJobObjectW(IntPtr.Zero, null);
        if (handle == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        var information = new ExtendedLimitInformation();
        information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        int length = Marshal.SizeOf(typeof(ExtendedLimitInformation));
        IntPtr buffer = Marshal.AllocHGlobal(length);
        try {
            Marshal.StructureToPtr(information, buffer, false);
            if (!SetInformationJobObject(handle, 9, buffer, (uint)length)) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
        }
        catch {
            CloseHandle(handle);
            handle = IntPtr.Zero;
            throw;
        }
        finally {
            Marshal.FreeHGlobal(buffer);
        }
    }

    public void Add(Process process) {
        if (handle == IntPtr.Zero) throw new ObjectDisposedException("FairyDevelopmentProcessJob");
        if (!AssignProcessToJobObject(handle, process.Handle)) {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
    }

    public void Dispose() {
        if (handle == IntPtr.Zero) return;
        CloseHandle(handle);
        handle = IntPtr.Zero;
        GC.SuppressFinalize(this);
    }

    ~FairyDevelopmentProcessJob() { Dispose(); }
}
'@
}

function New-DesktopPathAlias {
    param([Parameter(Mandatory = $true)][string]$Root)

    if ($Root -cmatch '^[\x00-\x7F]+$' -and -not $Root.Contains("~")) {
        return [PSCustomObject]@{ Root = $Root; Drive = $null }
    }

    $subst = Get-Command subst.exe -ErrorAction Stop
    foreach ($letter in @("Q", "R", "S", "T", "U", "W", "X", "Y", "Z")) {
        $drive = "${letter}:"
        if (Test-Path -LiteralPath "${drive}\") {
            continue
        }
        & $subst.Source $drive $Root
        if ($LASTEXITCODE -eq 0) {
            return [PSCustomObject]@{ Root = "${drive}\"; Drive = $drive }
        }
    }
    throw "No free drive letter is available for the Fairy desktop path alias"
}

function Resolve-FairyVoiceDevelopmentRuntime {
    param([Parameter(Mandatory = $true)][string]$Root)

    $programOverride = [Environment]::GetEnvironmentVariable(
        "FAIRY_VOICE_WORKER_PROGRAM",
        [EnvironmentVariableTarget]::Process
    )
    $cosyVoiceOverride = [Environment]::GetEnvironmentVariable(
        "FAIRY_COSYVOICE_ROOT",
        [EnvironmentVariableTarget]::Process
    )
    if ($programOverride -or $cosyVoiceOverride) {
        if (-not $programOverride -or -not $cosyVoiceOverride) {
            throw (
                "FAIRY_VOICE_WORKER_PROGRAM and FAIRY_COSYVOICE_ROOT must be " +
                "configured together"
            )
        }
        if (-not (Test-Path -LiteralPath $programOverride -PathType Leaf)) {
            throw "Configured Fairy Voice Worker program does not exist: $programOverride"
        }
        if (-not (Test-Path -LiteralPath $cosyVoiceOverride -PathType Container)) {
            throw "Configured CosyVoice root does not exist: $cosyVoiceOverride"
        }
        return [PSCustomObject]@{
            Program = [System.IO.Path]::GetFullPath($programOverride)
            CosyVoiceRoot = [System.IO.Path]::GetFullPath($cosyVoiceOverride)
        }
    }

    $directory = Get-Item -LiteralPath $Root -ErrorAction Stop
    while ($null -ne $directory) {
        $program = Join-Path $directory.FullName "cosyvoice_env\Scripts\python.exe"
        $cosyVoiceRoot = Join-Path $directory.FullName "third_party\CosyVoice"
        if (
            (Test-Path -LiteralPath $program -PathType Leaf) -and
            (Test-Path -LiteralPath $cosyVoiceRoot -PathType Container)
        ) {
            return [PSCustomObject]@{
                Program = [System.IO.Path]::GetFullPath($program)
                CosyVoiceRoot = [System.IO.Path]::GetFullPath($cosyVoiceRoot)
            }
        }
        $directory = $directory.Parent
    }
    return $null
}

$profilesPath = Join-Path $repositoryRoot "config\openrouter.providers.json"

$profiles = Get-Content -LiteralPath $profilesPath -Raw
$null = $profiles | ConvertFrom-Json -ErrorAction Stop
$key = $null
if ($OpenRouterKeyFile) {
    $resolvedKeyFile = Resolve-Path -LiteralPath $OpenRouterKeyFile -ErrorAction Stop
    if (-not (Test-Path -LiteralPath $resolvedKeyFile -PathType Leaf)) {
        throw "OpenRouter key file is not a regular file"
    }
    $key = (Get-Content -LiteralPath $resolvedKeyFile -Raw).Trim()
    if ($key.Length -lt 20) {
        throw "OpenRouter key file is empty or malformed"
    }
}

$environmentNames = @(
    "FAIRY_PROVIDER_PROFILES_JSON",
    "FAIRY_PROVIDER_SECRET_REFS_JSON",
    "FAIRY_PROVIDER_SECRET_OPENROUTER",
    "FAIRY_VOICE_WORKER_PROGRAM",
    "FAIRY_COSYVOICE_ROOT"
)
$previousEnvironment = @{}
$pathAlias = $null
$developmentJob = $null
$tauriProcess = $null
$voiceRuntime = Resolve-FairyVoiceDevelopmentRuntime -Root $repositoryRoot
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable(
        $name,
        [EnvironmentVariableTarget]::Process
    )
}

try {
    $env:FAIRY_PROVIDER_PROFILES_JSON = $profiles
    if ($null -ne $key) {
        $env:FAIRY_PROVIDER_SECRET_REFS_JSON = '{"openrouter":"FAIRY_PROVIDER_SECRET_OPENROUTER"}'
        $env:FAIRY_PROVIDER_SECRET_OPENROUTER = $key
    }
    else {
        Remove-Item Env:FAIRY_PROVIDER_SECRET_REFS_JSON -ErrorAction SilentlyContinue
        Remove-Item Env:FAIRY_PROVIDER_SECRET_OPENROUTER -ErrorAction SilentlyContinue
    }
    if ($null -ne $voiceRuntime) {
        try {
            & (Join-Path $PSScriptRoot "check-voice-runtime.ps1") `
                -PythonPath $voiceRuntime.Program |
                Out-Null
            Write-Host "Fairy Voice CUDA runtime preflight passed."
        }
        catch {
            Write-Warning "Fairy Voice runtime preflight failed: $($_.Exception.Message)"
        }
        $env:FAIRY_VOICE_WORKER_PROGRAM = $voiceRuntime.Program
        $env:FAIRY_COSYVOICE_ROOT = $voiceRuntime.CosyVoiceRoot
        Write-Host "Fairy Voice runtime: $($voiceRuntime.Program)"
    }
    else {
        Remove-Item Env:FAIRY_VOICE_WORKER_PROGRAM -ErrorAction SilentlyContinue
        Remove-Item Env:FAIRY_COSYVOICE_ROOT -ErrorAction SilentlyContinue
        Write-Warning (
            "Fairy Voice runtime was not found. Expected cosyvoice_env and " +
            "third_party\CosyVoice in the repository ancestry."
        )
    }

    $omniStageRoot = Join-Path $repositoryRoot "desktop\src-tauri\runtime\omni"
    $omniExecutable = Join-Path $omniStageRoot "fairy-omni-runtime.exe"
    $omniProfile = Join-Path $omniStageRoot "build-profile.txt"
    if ((Test-Path -LiteralPath $omniExecutable -PathType Leaf) -and
        (Test-Path -LiteralPath $omniProfile -PathType Leaf)) {
        $stagedOmniProfile = (Get-Content -LiteralPath $omniProfile -Raw -Encoding UTF8).Trim()
        Write-Host "Fairy Omni staged profile: $stagedOmniProfile (on-demand only)"
    }
    else {
        Write-Host "Fairy Omni is not staged; Local Beta remains unavailable."
    }

    $pathAlias = New-DesktopPathAlias -Root $repositoryRoot
    if ($Toggle) {
        Write-FairyLauncherState `
            -Path $launcherStatePath `
            -InstanceId $launcherInstanceId `
            -OwnerStartTicks $launcherStartTicks `
            -StartedAtUtc $launcherStartedAtUtc `
            -PathAliasDrive $pathAlias.Drive
    }
    $desktopPath = Join-Path $pathAlias.Root "desktop"
    $cargo = (Get-Command cargo.exe -ErrorAction Stop).Source
    $realtimeManifest = Join-Path $desktopPath "src-tauri\Cargo.toml"
    Write-Host "Preparing Fairy Realtime Worker..."
    & $cargo build `
        --manifest-path $realtimeManifest `
        --package fairy-realtime-worker
    if ($LASTEXITCODE -ne 0) {
        throw "Fairy Realtime Worker development build failed"
    }
    $tauriCli = Join-Path $desktopPath "node_modules\@tauri-apps\cli\tauri.js"
    if (-not (Test-Path -LiteralPath $tauriCli -PathType Leaf)) {
        throw "The local Tauri CLI is unavailable; run npm install in desktop first"
    }
    $node = (Get-Command node.exe -ErrorAction Stop).Source
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $node
    $startInfo.Arguments = "`"$tauriCli`" dev"
    $startInfo.WorkingDirectory = $desktopPath
    $startInfo.UseShellExecute = $false
    $developmentJob = [FairyDevelopmentProcessJob]::new()
    try {
        $tauriProcess = [System.Diagnostics.Process]::Start($startInfo)
        if ($null -eq $tauriProcess) {
            throw "Tauri development process did not start"
        }
        $developmentJob.Add($tauriProcess)
        $stopRequested = $false
        while (-not $tauriProcess.WaitForExit(250)) {
            if ($Toggle -and (
                Test-FairyLauncherStopRequested -InstanceId $launcherInstanceId
            )) {
                $stopRequested = $true
                break
            }
        }
        if ($stopRequested) {
            Write-Host "Fairy development environment received a stop request."
        }
        elseif ($tauriProcess.ExitCode -ne 0) {
            throw "Tauri development process exited with code $($tauriProcess.ExitCode)"
        }
    }
    catch {
        if ($null -ne $tauriProcess -and -not $tauriProcess.HasExited) {
            Stop-Process -Id $tauriProcess.Id -Force -ErrorAction SilentlyContinue
        }
        throw
    }
}
finally {
    if ($null -ne $developmentJob) {
        $developmentJob.Dispose()
        $developmentJob = $null
    }
    if ($null -ne $tauriProcess) {
        $tauriProcess.Dispose()
        $tauriProcess = $null
    }
    if ($null -ne $pathAlias -and $null -ne $pathAlias.Drive) {
        & subst.exe $($pathAlias.Drive) /D
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Could not remove desktop workspace alias $($pathAlias.Drive)"
        }
    }
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $previousEnvironment[$name],
            [EnvironmentVariableTarget]::Process
        )
    }
    $key = $null
}
}
finally {
    if ($launcherOwnsState) {
        Remove-FairyLauncherFile `
            -Path $launcherStatePath `
            -ExpectedInstanceId $launcherInstanceId
        Remove-FairyLauncherFile `
            -Path $launcherStopPath `
            -ExpectedInstanceId $launcherInstanceId
    }
}
