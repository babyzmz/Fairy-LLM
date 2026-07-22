[CmdletBinding()]
param(
    [string]$Executable,
    [ValidateSet(60, 144, 300)]
    [int]$TargetFps = 60,
    [ValidateRange(1, 300)]
    [int]$DurationSeconds = 10,
    [string]$OutputPath,
    [string]$ScreenshotPath,
    [switch]$DiagnosticSolid,
    [switch]$RestrictedWebViewRunner,
    [switch]$VerifyCadence,
    [ValidateRange(0, 10)]
    [int]$RestartCycles = 0,
    [switch]$VerifyLiveBackdrop,
    [switch]$SkipClickProbe,
    [switch]$KeepRunning
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Executable)) {
    $Executable = Join-Path $root "desktop\src-tauri\target\debug\fairy.exe"
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $root ".tmp\presence-native-gpu-$TargetFps.json"
}
if ([string]::IsNullOrWhiteSpace($ScreenshotPath)) {
    $ScreenshotPath = Join-Path $root ".tmp\presence-native-gpu-$TargetFps.png"
}

function Test-NativeExecutableStale([string]$BinaryPath) {
    if (-not (Test-Path -LiteralPath $BinaryPath -PathType Leaf)) { return $true }
    $binaryWrite = (Get-Item -LiteralPath $BinaryPath).LastWriteTimeUtc
    $manifestRoot = Join-Path $root "desktop\src-tauri"
    $inputs = @(
        (Get-Item -LiteralPath (Join-Path $manifestRoot "Cargo.toml")),
        (Get-Item -LiteralPath (Join-Path $manifestRoot "Cargo.lock")),
        (Get-Item -LiteralPath (Join-Path $manifestRoot "build.rs")),
        (Get-Item -LiteralPath (Join-Path $manifestRoot "tauri.conf.json"))
    )
    $inputs += Get-ChildItem -LiteralPath (Join-Path $manifestRoot "src") -Recurse -File |
        Where-Object Extension -in ".rs", ".hlsl"
    $inputs += Get-ChildItem -LiteralPath (Join-Path $manifestRoot "crates") -Recurse -File |
        Where-Object {
            $_.Extension -in ".rs", ".hlsl" -or
            $_.Name -in "Cargo.toml", "Cargo.lock", "build.rs"
        }
    return ($inputs | Where-Object LastWriteTimeUtc -gt $binaryWrite | Select-Object -First 1) -ne $null
}

function Normalize-ProcessPathEnvironment {
    $pathValue = [System.Environment]::GetEnvironmentVariable(
        "PATH",
        [System.EnvironmentVariableTarget]::Process
    )
    if ([string]::IsNullOrWhiteSpace($pathValue)) { return }
    # Codex can provide both PATH and Path. ProcessStartInfo uses a case-insensitive map on
    # Windows PowerShell 5 and rejects that duplicate. This only changes this test process.
    [System.Environment]::SetEnvironmentVariable(
        "PATH",
        $null,
        [System.EnvironmentVariableTarget]::Process
    )
    [System.Environment]::SetEnvironmentVariable(
        "Path",
        $pathValue,
        [System.EnvironmentVariableTarget]::Process
    )
}

function Test-LoopbackPort([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connect = $client.ConnectAsync([System.Net.IPAddress]::Loopback, $Port)
        return $connect.Wait(250) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Wait-LoopbackPort(
    [System.Diagnostics.Process]$Process,
    [int]$Port,
    [int]$TimeoutSeconds = 30
) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "Vite development server exited before opening port ${Port}: $($Process.ExitCode)"
        }
        if (Test-LoopbackPort $Port) { return }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for Vite development server on port ${Port}"
}

function Start-ViteDevelopmentServer {
    $node = (Get-Command node -ErrorAction Stop).Source
    $vite = (Resolve-Path -LiteralPath (Join-Path $root "desktop\node_modules\vite\bin\vite.js")).Path
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $node
    $startInfo.Arguments = "`"$vite`" --host 127.0.0.1 --port 1430 --strictPort"
    $startInfo.WorkingDirectory = Join-Path $root "desktop"
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $server = [System.Diagnostics.Process]::Start($startInfo)
    if ($null -eq $server) { throw "Vite development server did not start" }
    Wait-LoopbackPort $server 1430
    return $server
}

if (Test-NativeExecutableStale $Executable) {
    & cargo build --manifest-path (Join-Path $root "desktop\src-tauri\Cargo.toml")
    if ($LASTEXITCODE -ne 0) { throw "Fairy debug build failed" }
}
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Fairy executable is missing after debug build: $Executable"
}

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public sealed class FairyNativeGpuWindow {
    public IntPtr Handle { get; set; }
    public string Title { get; set; }
    public string ClassName { get; set; }
    public int X { get; set; }
    public int Y { get; set; }
    public int Width { get; set; }
    public int Height { get; set; }
    public long ExtendedStyle { get; set; }
}

public static class FairyNativeGpuProbe {
    private delegate bool EnumWindowsCallback(IntPtr window, IntPtr parameter);
    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)]
    private struct POINT { public int X, Y; }
    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsCallback callback, IntPtr parameter);
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextW(IntPtr window, StringBuilder text, int capacity);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassNameW(IntPtr window, StringBuilder text, int capacity);
    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr window, out RECT rectangle);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")]
    private static extern IntPtr GetWindowLongPtr(IntPtr window, int index);
    [DllImport("user32.dll")]
    private static extern IntPtr SendMessageW(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")]
    private static extern IntPtr WindowFromPoint(POINT point);
    [DllImport("user32.dll")]
    private static extern IntPtr GetAncestor(IntPtr window, uint flags);
    [DllImport("user32.dll")]
    private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr dpiContext);

    public static void EnablePerMonitorV2() {
        SetThreadDpiAwarenessContext(new IntPtr(-4));
    }

    public static List<FairyNativeGpuWindow> WindowsForProcess(int expectedProcessId) {
        var result = new List<FairyNativeGpuWindow>();
        EnumWindows((window, _) => {
            uint processId;
            GetWindowThreadProcessId(window, out processId);
            if (processId != (uint)expectedProcessId) return true;
            var text = new StringBuilder(256);
            var className = new StringBuilder(256);
            GetWindowTextW(window, text, text.Capacity);
            GetClassNameW(window, className, className.Capacity);
            RECT rectangle;
            GetWindowRect(window, out rectangle);
            result.Add(new FairyNativeGpuWindow {
                Handle = window,
                Title = text.ToString(),
                ClassName = className.ToString(),
                X = rectangle.Left,
                Y = rectangle.Top,
                Width = rectangle.Right - rectangle.Left,
                Height = rectangle.Bottom - rectangle.Top,
                ExtendedStyle = GetWindowLongPtr(window, -20).ToInt64(),
            });
            return true;
        }, IntPtr.Zero);
        return result;
    }

    public static bool ReturnsTransparentHitTest(FairyNativeGpuWindow window) {
        long packed = ((long)(window.Y + window.Height / 2) << 16) |
            (uint)(window.X + window.Width / 2) & 0xFFFF;
        return SendMessageW(window.Handle, 0x0084, IntPtr.Zero, new IntPtr(packed)).ToInt64() == -1;
    }

    public static IntPtr WindowAt(int x, int y) {
        return WindowFromPoint(new POINT { X = x, Y = y });
    }

    public static IntPtr RootWindowAt(int x, int y) {
        return GetAncestor(WindowAt(x, y), 2);
    }
}

public sealed class FairyClickSentinel : IDisposable {
    const uint WS_POPUP = 0x80000000;
    const uint WS_EX_TOOLWINDOW = 0x00000080;
    const uint WS_EX_NOACTIVATE = 0x08000000;
    const uint WS_EX_TOPMOST = 0x00000008;
    const int SW_SHOWNOACTIVATE = 4;
    const uint WM_CLOSE = 0x0010;
    const uint WM_DESTROY = 0x0002;
    const uint WM_LBUTTONUP = 0x0202;
    const uint INPUT_MOUSE = 0;
    const uint MOUSEEVENTF_MOVE = 0x0001;
    const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    const uint MOUSEEVENTF_LEFTUP = 0x0004;
    const uint MOUSEEVENTF_VIRTUALDESK = 0x4000;
    const uint MOUSEEVENTF_ABSOLUTE = 0x8000;
    const int SM_XVIRTUALSCREEN = 76;
    const int SM_YVIRTUALSCREEN = 77;
    const int SM_CXVIRTUALSCREEN = 78;
    const int SM_CYVIRTUALSCREEN = 79;
    const uint SWP_NOSIZE = 0x0001;
    const uint SWP_NOMOVE = 0x0002;
    const uint SWP_NOACTIVATE = 0x0010;

    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    delegate IntPtr WindowProc(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct WNDCLASSEX {
        public uint cbSize;
        public uint style;
        public WindowProc lpfnWndProc;
        public int cbClsExtra;
        public int cbWndExtra;
        public IntPtr hInstance;
        public IntPtr hIcon;
        public IntPtr hCursor;
        public IntPtr hbrBackground;
        public string lpszMenuName;
        public string lpszClassName;
        public IntPtr hIconSm;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct POINT { public int X, Y; }

    [StructLayout(LayoutKind.Sequential)]
    struct RECT { public int Left, Top, Right, Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    struct MOUSEINPUT {
        public int dx;
        public int dy;
        public uint mouseData;
        public uint dwFlags;
        public uint time;
        public UIntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Explicit)]
    struct INPUTUNION {
        [FieldOffset(0)] public MOUSEINPUT mouse;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct INPUT {
        public uint type;
        public INPUTUNION data;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct MSG {
        public IntPtr hwnd;
        public uint message;
        public UIntPtr wParam;
        public IntPtr lParam;
        public uint time;
        public POINT point;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr GetModuleHandleW(string moduleName);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern ushort RegisterClassExW(ref WNDCLASSEX windowClass);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateWindowExW(
        uint extendedStyle,
        string className,
        string title,
        uint style,
        int x,
        int y,
        int width,
        int height,
        IntPtr parent,
        IntPtr menu,
        IntPtr instance,
        IntPtr parameter
    );
    [DllImport("user32.dll")]
    static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")]
    static extern bool UpdateWindow(IntPtr window);
    [DllImport("user32.dll")]
    static extern int GetMessageW(out MSG message, IntPtr window, uint minimum, uint maximum);
    [DllImport("user32.dll")]
    static extern bool TranslateMessage(ref MSG message);
    [DllImport("user32.dll")]
    static extern IntPtr DispatchMessageW(ref MSG message);
    [DllImport("user32.dll")]
    static extern IntPtr DefWindowProcW(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")]
    static extern bool DestroyWindow(IntPtr window);
    [DllImport("user32.dll")]
    static extern void PostQuitMessage(int exitCode);
    [DllImport("user32.dll")]
    static extern bool PostMessageW(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")]
    static extern bool GetPhysicalCursorPos(out POINT point);
    [DllImport("user32.dll", SetLastError = true)]
    static extern bool SetPhysicalCursorPos(int x, int y);
    [DllImport("user32.dll", SetLastError = true)]
    static extern bool GetClipCursor(out RECT rectangle);
    [DllImport("user32.dll", EntryPoint = "ClipCursor", SetLastError = true)]
    static extern bool ClearClipCursor(IntPtr rectangle);
    [DllImport("user32.dll", EntryPoint = "ClipCursor", SetLastError = true)]
    static extern bool RestoreClipCursor(ref RECT rectangle);
    [DllImport("user32.dll")]
    static extern bool GetWindowRect(IntPtr window, out RECT rectangle);
    [DllImport("user32.dll", SetLastError = true)]
    static extern uint SendInput(uint inputCount, INPUT[] inputs, int inputSize);
    [DllImport("user32.dll")]
    static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll", SetLastError = true)]
    static extern bool SetWindowPos(
        IntPtr window,
        IntPtr insertAfter,
        int x,
        int y,
        int width,
        int height,
        uint flags
    );
    [DllImport("user32.dll")]
    static extern IntPtr SetThreadDpiAwarenessContext(IntPtr dpiContext);

    readonly int x;
    readonly int y;
    readonly int width;
    readonly int height;
    readonly string className = "FairyClickSentinel_" + Guid.NewGuid().ToString("N");
    readonly AutoResetEvent ready = new AutoResetEvent(false);
    readonly ManualResetEventSlim clicked = new ManualResetEventSlim(false);
    readonly Thread thread;
    readonly WindowProc windowProc;
    IntPtr window;
    Exception startupError;

    public FairyClickSentinel(int x, int y, int width, int height) {
        this.x = x;
        this.y = y;
        this.width = width;
        this.height = height;
        windowProc = HandleMessage;
        thread = new Thread(Run) { IsBackground = true, Name = "Fairy click-through sentinel" };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        if (!ready.WaitOne(TimeSpan.FromSeconds(5))) {
            throw new TimeoutException("Click-through sentinel did not create its window");
        }
        if (startupError != null) throw new InvalidOperationException("Click-through sentinel failed", startupError);
    }

    public IntPtr Handle { get { return window; } }

    public string Frame() {
        RECT rectangle;
        if (!GetWindowRect(window, out rectangle)) return "unavailable";
        return string.Format(
            "{0},{1} {2}x{3}",
            rectangle.Left,
            rectangle.Top,
            rectangle.Right - rectangle.Left,
            rectangle.Bottom - rectangle.Top
        );
    }

    public bool ClickAndWait(int screenX, int screenY, int timeoutMilliseconds) {
        clicked.Reset();
        POINT original;
        var restoreCursor = GetPhysicalCursorPos(out original);
        RECT originalClip;
        var restoreClip = GetClipCursor(out originalClip);
        try {
            if (!ClearClipCursor(IntPtr.Zero)) {
                throw new System.ComponentModel.Win32Exception(
                    Marshal.GetLastWin32Error(),
                    "ClipCursor failed while preparing the click-through probe"
                );
            }
            for (var attempt = 0; attempt < 2; attempt++) {
                var positioned = PositionCursor(screenX, screenY);
                if (
                    Math.Abs(positioned.X - screenX) > 8 ||
                    Math.Abs(positioned.Y - screenY) > 8) {
                    throw new InvalidOperationException(string.Format(
                        "Physical cursor mismatch: expected={0},{1}; actual={2},{3}; clip={4},{5} {6}x{7}",
                        screenX,
                        screenY,
                        positioned.X,
                        positioned.Y,
                        originalClip.Left,
                        originalClip.Top,
                        originalClip.Right - originalClip.Left,
                        originalClip.Bottom - originalClip.Top
                    ));
                }
                clicked.Reset();
                SendInputs(new[] {
                    MouseInput(0, 0, MOUSEEVENTF_LEFTDOWN),
                    MouseInput(0, 0, MOUSEEVENTF_LEFTUP),
                });
                if (clicked.Wait(timeoutMilliseconds / 2)) return true;
                Thread.Sleep(60);
            }
            return false;
        }
        finally {
            if (restoreCursor) SetPhysicalCursorPos(original.X, original.Y);
            if (restoreClip) RestoreClipCursor(ref originalClip);
        }
    }

    static POINT PositionCursor(int screenX, int screenY) {
        POINT positioned;
        if (SetPhysicalCursorPos(screenX, screenY)) {
            Thread.Sleep(20);
            if (GetPhysicalCursorPos(out positioned) &&
                Math.Abs(positioned.X - screenX) <= 8 &&
                Math.Abs(positioned.Y - screenY) <= 8) {
                return positioned;
            }
        }

        // Remote Desktop and mixed-DPI input desktops can acknowledge SetPhysicalCursorPos
        // while mapping it through a logical coordinate space. Absolute virtual-desktop input
        // uses the documented 0..65535 range and avoids that virtualization path.
        var virtualX = GetSystemMetrics(SM_XVIRTUALSCREEN);
        var virtualY = GetSystemMetrics(SM_YVIRTUALSCREEN);
        var virtualWidth = Math.Max(2, GetSystemMetrics(SM_CXVIRTUALSCREEN));
        var virtualHeight = Math.Max(2, GetSystemMetrics(SM_CYVIRTUALSCREEN));
        var absoluteX = (int)Math.Max(0, Math.Min(
            65535,
            ((long)screenX - virtualX) * 65535L / (virtualWidth - 1)
        ));
        var absoluteY = (int)Math.Max(0, Math.Min(
            65535,
            ((long)screenY - virtualY) * 65535L / (virtualHeight - 1)
        ));
        SendInputs(new[] {
            MouseInput(
                absoluteX,
                absoluteY,
                MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
            ),
        });
        Thread.Sleep(30);
        if (!GetPhysicalCursorPos(out positioned)) {
            throw new System.ComponentModel.Win32Exception(
                Marshal.GetLastWin32Error(),
                "GetPhysicalCursorPos failed after absolute cursor placement"
            );
        }
        return positioned;
    }

    static INPUT MouseInput(int x, int y, uint flags) {
        return new INPUT {
            type = INPUT_MOUSE,
            data = new INPUTUNION {
                mouse = new MOUSEINPUT {
                    dx = x,
                    dy = y,
                    dwFlags = flags,
                    dwExtraInfo = UIntPtr.Zero,
                },
            },
        };
    }

    static void SendInputs(INPUT[] inputs) {
        var inserted = SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(INPUT)));
        if (inserted != inputs.Length) {
            throw new System.ComponentModel.Win32Exception(
                Marshal.GetLastWin32Error(),
                string.Format("SendInput inserted {0} of {1} mouse events", inserted, inputs.Length)
            );
        }
    }

    public void PlaceBehindGroup(IntPtr renderWindow, IntPtr nativeSurface, IntPtr inputWindow) {
        if (window == IntPtr.Zero) throw new InvalidOperationException("Click-through sentinel is unavailable");
        var topmost = new IntPtr(-1);
        var ordered = new[] { window, renderWindow, nativeSurface, inputWindow };
        foreach (var target in ordered) {
            if (!SetWindowPos(
                target,
                topmost,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            )) {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            }
        }
    }

    void Run() {
        try {
            SetThreadDpiAwarenessContext(new IntPtr(-4));
            var instance = GetModuleHandleW(null);
            var definition = new WNDCLASSEX {
                cbSize = (uint)Marshal.SizeOf(typeof(WNDCLASSEX)),
                lpfnWndProc = windowProc,
                hInstance = instance,
                lpszClassName = className,
            };
            if (RegisterClassExW(ref definition) == 0) {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            }
            window = CreateWindowExW(
                WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST,
                className,
                string.Empty,
                WS_POPUP,
                x,
                y,
                width,
                height,
                IntPtr.Zero,
                IntPtr.Zero,
                instance,
                IntPtr.Zero
            );
            if (window == IntPtr.Zero) {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            }
            ShowWindow(window, SW_SHOWNOACTIVATE);
            UpdateWindow(window);
        }
        catch (Exception error) {
            startupError = error;
        }
        finally {
            ready.Set();
        }
        if (window == IntPtr.Zero) return;
        MSG message;
        while (GetMessageW(out message, IntPtr.Zero, 0, 0) > 0) {
            TranslateMessage(ref message);
            DispatchMessageW(ref message);
        }
    }

    IntPtr HandleMessage(IntPtr target, uint message, IntPtr wParam, IntPtr lParam) {
        if (message == WM_LBUTTONUP) clicked.Set();
        if (message == WM_CLOSE) {
            DestroyWindow(target);
            return IntPtr.Zero;
        }
        if (message == WM_DESTROY) {
            PostQuitMessage(0);
            return IntPtr.Zero;
        }
        return DefWindowProcW(target, message, wParam, lParam);
    }

    public void Dispose() {
        if (window != IntPtr.Zero) PostMessageW(window, WM_CLOSE, IntPtr.Zero, IntPtr.Zero);
        if (Thread.CurrentThread != thread) thread.Join(2000);
        clicked.Dispose();
        ready.Dispose();
        GC.SuppressFinalize(this);
    }
}

public sealed class FairyColorBackdrop : IDisposable {
    const uint WS_POPUP = 0x80000000;
    const uint WS_EX_TOOLWINDOW = 0x00000080;
    const uint WS_EX_NOACTIVATE = 0x08000000;
    const uint WS_EX_TOPMOST = 0x00000008;
    const int SW_SHOWNOACTIVATE = 4;
    const uint WM_CLOSE = 0x0010;
    const uint WM_DESTROY = 0x0002;
    const uint WM_ERASEBKGND = 0x0014;
    const uint WM_PAINT = 0x000F;
    const uint SWP_NOSIZE = 0x0001;
    const uint SWP_NOMOVE = 0x0002;
    const uint SWP_NOACTIVATE = 0x0010;

    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    delegate IntPtr WindowProc(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct WNDCLASSEX {
        public uint cbSize;
        public uint style;
        public WindowProc lpfnWndProc;
        public int cbClsExtra;
        public int cbWndExtra;
        public IntPtr hInstance;
        public IntPtr hIcon;
        public IntPtr hCursor;
        public IntPtr hbrBackground;
        public string lpszMenuName;
        public string lpszClassName;
        public IntPtr hIconSm;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct POINT { public int X, Y; }

    [StructLayout(LayoutKind.Sequential)]
    struct RECT { public int Left, Top, Right, Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    struct PAINTSTRUCT {
        public IntPtr hdc;
        [MarshalAs(UnmanagedType.Bool)] public bool erase;
        public RECT paint;
        [MarshalAs(UnmanagedType.Bool)] public bool restore;
        [MarshalAs(UnmanagedType.Bool)] public bool update;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 32)] public byte[] reserved;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct MSG {
        public IntPtr hwnd;
        public uint message;
        public UIntPtr wParam;
        public IntPtr lParam;
        public uint time;
        public POINT point;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr GetModuleHandleW(string moduleName);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern ushort RegisterClassExW(ref WNDCLASSEX windowClass);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateWindowExW(
        uint extendedStyle,
        string className,
        string title,
        uint style,
        int x,
        int y,
        int width,
        int height,
        IntPtr parent,
        IntPtr menu,
        IntPtr instance,
        IntPtr parameter
    );
    [DllImport("user32.dll")]
    static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")]
    static extern bool UpdateWindow(IntPtr window);
    [DllImport("user32.dll")]
    static extern int GetMessageW(out MSG message, IntPtr window, uint minimum, uint maximum);
    [DllImport("user32.dll")]
    static extern bool TranslateMessage(ref MSG message);
    [DllImport("user32.dll")]
    static extern IntPtr DispatchMessageW(ref MSG message);
    [DllImport("user32.dll")]
    static extern IntPtr DefWindowProcW(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")]
    static extern bool DestroyWindow(IntPtr window);
    [DllImport("user32.dll")]
    static extern void PostQuitMessage(int exitCode);
    [DllImport("user32.dll")]
    static extern bool PostMessageW(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll", SetLastError = true)]
    static extern bool SetWindowPos(
        IntPtr window,
        IntPtr insertAfter,
        int x,
        int y,
        int width,
        int height,
        uint flags
    );
    [DllImport("user32.dll")]
    static extern IntPtr SetThreadDpiAwarenessContext(IntPtr dpiContext);
    [DllImport("user32.dll")]
    static extern IntPtr BeginPaint(IntPtr window, out PAINTSTRUCT paint);
    [DllImport("user32.dll")]
    static extern bool EndPaint(IntPtr window, ref PAINTSTRUCT paint);
    [DllImport("user32.dll")]
    static extern int FillRect(IntPtr deviceContext, ref RECT rectangle, IntPtr brush);
    [DllImport("user32.dll")]
    static extern bool InvalidateRect(IntPtr window, IntPtr rectangle, bool erase);
    [DllImport("gdi32.dll")]
    static extern IntPtr CreateSolidBrush(uint color);
    [DllImport("gdi32.dll")]
    static extern bool DeleteObject(IntPtr value);

    readonly int x;
    readonly int y;
    readonly int width;
    readonly int height;
    readonly string className = "FairyColorBackdrop_" + Guid.NewGuid().ToString("N");
    readonly AutoResetEvent ready = new AutoResetEvent(false);
    readonly Thread thread;
    readonly WindowProc windowProc;
    volatile uint color;
    IntPtr window;
    Exception startupError;

    public FairyColorBackdrop(int x, int y, int width, int height) {
        this.x = x;
        this.y = y;
        this.width = width;
        this.height = height;
        color = ColorRef(128, 24, 32);
        windowProc = HandleMessage;
        thread = new Thread(Run) { IsBackground = true, Name = "Fairy live backdrop sentinel" };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        if (!ready.WaitOne(TimeSpan.FromSeconds(5))) {
            throw new TimeoutException("Live backdrop sentinel did not create its window");
        }
        if (startupError != null) {
            throw new InvalidOperationException("Live backdrop sentinel failed", startupError);
        }
    }

    public IntPtr Handle { get { return window; } }

    public void SetColor(byte red, byte green, byte blue) {
        color = ColorRef(red, green, blue);
        InvalidateRect(window, IntPtr.Zero, false);
        UpdateWindow(window);
    }

    public void PlaceBehindGroup(IntPtr renderWindow, IntPtr nativeSurface, IntPtr inputWindow) {
        var topmost = new IntPtr(-1);
        var ordered = new[] { window, renderWindow, nativeSurface, inputWindow };
        foreach (var target in ordered) {
            if (!SetWindowPos(
                target,
                topmost,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            )) {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            }
        }
    }

    static uint ColorRef(byte red, byte green, byte blue) {
        return (uint)(red | (green << 8) | (blue << 16));
    }

    void Run() {
        try {
            SetThreadDpiAwarenessContext(new IntPtr(-4));
            var instance = GetModuleHandleW(null);
            var definition = new WNDCLASSEX {
                cbSize = (uint)Marshal.SizeOf(typeof(WNDCLASSEX)),
                lpfnWndProc = windowProc,
                hInstance = instance,
                lpszClassName = className,
            };
            if (RegisterClassExW(ref definition) == 0) {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            }
            window = CreateWindowExW(
                WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST,
                className,
                string.Empty,
                WS_POPUP,
                x,
                y,
                width,
                height,
                IntPtr.Zero,
                IntPtr.Zero,
                instance,
                IntPtr.Zero
            );
            if (window == IntPtr.Zero) {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            }
            ShowWindow(window, SW_SHOWNOACTIVATE);
            UpdateWindow(window);
        }
        catch (Exception error) {
            startupError = error;
        }
        finally {
            ready.Set();
        }
        if (window == IntPtr.Zero) return;
        MSG message;
        while (GetMessageW(out message, IntPtr.Zero, 0, 0) > 0) {
            TranslateMessage(ref message);
            DispatchMessageW(ref message);
        }
    }

    IntPtr HandleMessage(IntPtr target, uint message, IntPtr wParam, IntPtr lParam) {
        if (message == WM_ERASEBKGND) return new IntPtr(1);
        if (message == WM_PAINT) {
            PAINTSTRUCT paint;
            var deviceContext = BeginPaint(target, out paint);
            var rectangle = new RECT { Left = 0, Top = 0, Right = width, Bottom = height };
            var brush = CreateSolidBrush(color);
            if (brush != IntPtr.Zero) {
                FillRect(deviceContext, ref rectangle, brush);
                DeleteObject(brush);
            }
            EndPaint(target, ref paint);
            return IntPtr.Zero;
        }
        if (message == WM_CLOSE) {
            DestroyWindow(target);
            return IntPtr.Zero;
        }
        if (message == WM_DESTROY) {
            PostQuitMessage(0);
            return IntPtr.Zero;
        }
        return DefWindowProcW(target, message, wParam, lParam);
    }

    public void Dispose() {
        if (window != IntPtr.Zero) PostMessageW(window, WM_CLOSE, IntPtr.Zero, IntPtr.Zero);
        if (Thread.CurrentThread != thread) thread.Join(2000);
        ready.Dispose();
        GC.SuppressFinalize(this);
    }
}
'@

[FairyNativeGpuProbe]::EnablePerMonitorV2()

Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;

public sealed class FairyOwnedProcessJob : IDisposable {
    const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
    IntPtr handle;

    [StructLayout(LayoutKind.Sequential)]
    struct JobObjectBasicLimitInformation {
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
    struct JobObjectExtendedLimitInformation {
        public JobObjectBasicLimitInformation BasicLimitInformation;
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

    public FairyOwnedProcessJob() {
        handle = CreateJobObjectW(IntPtr.Zero, null);
        if (handle == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        var information = new JobObjectExtendedLimitInformation();
        information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        int length = Marshal.SizeOf(typeof(JobObjectExtendedLimitInformation));
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
        if (handle == IntPtr.Zero) throw new ObjectDisposedException("FairyOwnedProcessJob");
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

    ~FairyOwnedProcessJob() { Dispose(); }
}
'@

function Get-AvailablePort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port }
    finally { $listener.Stop() }
}

function Measure-RadialLuminance(
    $Image,
    [int]$CenterX,
    [int]$CenterY,
    [double]$Radius,
    [double]$Tolerance = 1.0
) {
    $maximumRadius = [int][Math]::Ceiling($Radius + $Tolerance)
    $sum = 0.0
    $samples = 0
    for ($y = [Math]::Max(0, $CenterY - $maximumRadius);
        $y -le [Math]::Min($Image.Height - 1, $CenterY + $maximumRadius);
        $y++) {
        for ($x = [Math]::Max(0, $CenterX - $maximumRadius);
            $x -le [Math]::Min($Image.Width - 1, $CenterX + $maximumRadius);
            $x++) {
            $distance = [Math]::Sqrt(
                [Math]::Pow($x - $CenterX, 2) + [Math]::Pow($y - $CenterY, 2)
            )
            if ([Math]::Abs($distance - $Radius) -gt $Tolerance) { continue }
            $pixel = $Image.GetPixel($x, $y)
            $sum += ($pixel.R + $pixel.G + $pixel.B) / 3.0
            $samples++
        }
    }
    if ($samples -lt 1) { throw "Identity radial sample area is empty" }
    return $sum / $samples
}

function Format-FairyProcessWindows([System.Diagnostics.Process]$Process) {
    return @(
        [FairyNativeGpuProbe]::WindowsForProcess($Process.Id) |
            ForEach-Object {
                "hwnd=0x$([Convert]::ToString($_.Handle.ToInt64(), 16)) title='$($_.Title)' class='$($_.ClassName)' frame=$($_.X),$($_.Y) $($_.Width)x$($_.Height) ex=0x$([Convert]::ToString($_.ExtendedStyle, 16))"
            }
    ) -join "; "
}

function Wait-FairyPresenceWebView(
    [System.Diagnostics.Process]$Process,
    [string]$Surface,
    [int]$ExpectedWidth,
    [int]$ExpectedHeight,
    [int]$TimeoutSeconds = 30
) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "Fairy exited before creating '${Surface}': $($Process.ExitCode)"
        }
        $expectedRatio = [double]$ExpectedWidth / [double]$ExpectedHeight
        $window = [FairyNativeGpuProbe]::WindowsForProcess($Process.Id) |
            Where-Object {
                if ($_.Width -le 0 -or $_.Height -le 0) { return $false }
                if ($_.ClassName -eq "FairyNativePresenceRendererClass") { return $false }
                if (-not [string]::IsNullOrEmpty($_.Title)) { return $false }
                $scaleX = [double]$_.Width / [double]$ExpectedWidth
                $scaleY = [double]$_.Height / [double]$ExpectedHeight
                $ratio = [double]$_.Width / [double]$_.Height
                return $scaleX -ge 0.75 -and $scaleX -le 4.0 -and
                    [Math]::Abs($scaleX - $scaleY) -le 0.08 -and
                    [Math]::Abs($ratio - $expectedRatio) -le 0.08
            } |
            Sort-Object {
                $scaleX = [double]$_.Width / [double]$ExpectedWidth
                $scaleY = [double]$_.Height / [double]$ExpectedHeight
                [Math]::Abs($scaleX - $scaleY) + [Math]::Abs($scaleX - 1.0)
            } |
            Select-Object -First 1
        if ($null -ne $window -and $window.Width -gt 0 -and $window.Height -gt 0) { return $window }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for '${Surface}' WebView. Process windows: $(Format-FairyProcessWindows $Process)"
}

function Wait-FairyWindowClass(
    [System.Diagnostics.Process]$Process,
    [string]$ClassName,
    [int]$TimeoutSeconds = 30
) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "Fairy exited before creating window class '${ClassName}': $($Process.ExitCode)"
        }
        $window = [FairyNativeGpuProbe]::WindowsForProcess($Process.Id) |
            Where-Object ClassName -eq $ClassName |
            Select-Object -First 1
        if ($null -ne $window -and $window.Width -gt 0 -and $window.Height -gt 0) { return $window }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    $knownClasses = @(
        [FairyNativeGpuProbe]::WindowsForProcess($Process.Id) |
            ForEach-Object ClassName |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    ) -join ", "
    throw "Timed out waiting for native window class '${ClassName}'. Process classes: ${knownClasses}"
}

function Stop-ProcessTree([System.Diagnostics.Process]$Target) {
    $Target.Refresh()
    if ($Target.HasExited) { return }
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & taskkill.exe /PID $Target.Id /T /F 2>&1 | Out-Null
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $Target.Refresh()
    if ($Target.HasExited) { return }
    if (-not $Target.WaitForExit(5000)) {
        $Target.Kill()
        $Target.WaitForExit()
    }
}

function Get-FairyProcessTree([int]$RootProcessId) {
    $all = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CommandLine, WorkingSetSize)
    $pending = [System.Collections.Generic.Queue[uint32]]::new()
    $seen = [System.Collections.Generic.HashSet[uint32]]::new()
    $pending.Enqueue([uint32]$RootProcessId)
    $rows = [System.Collections.Generic.List[object]]::new()
    while ($pending.Count -gt 0) {
        $parent = $pending.Dequeue()
        if (-not $seen.Add($parent)) { continue }
        foreach ($row in @($all | Where-Object { [uint32]$_.ProcessId -eq $parent })) {
            $rows.Add($row)
        }
        foreach ($child in @($all | Where-Object { [uint32]$_.ParentProcessId -eq $parent })) {
            $pending.Enqueue([uint32]$child.ProcessId)
        }
    }
    return @($rows)
}

function Remove-VerifiedScratch([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not ([System.IO.Path]::GetFileName($resolved)).StartsWith("fairy-native-gpu-", [StringComparison]::Ordinal)) {
        throw "Refusing to remove unverified scratch path: $resolved"
    }
    $item = Get-Item -Force -LiteralPath $resolved
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to remove reparse-point scratch path: $resolved"
    }
    for ($attempt = 1; $attempt -le 20; $attempt += 1) {
        try {
            Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction Stop
            return
        }
        catch {
            if ($attempt -eq 20) {
                Write-Warning "Native GPU scratch cleanup is deferred: $resolved"
                return
            }
            Start-Sleep -Milliseconds 250
        }
    }
}

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("fairy-native-gpu-" + [guid]::NewGuid().ToString("N"))
$fairyData = Join-Path $scratch "FairyData"
$webViewData = Join-Path $scratch "WebView2"
$preferencesDirectory = Join-Path $fairyData "preferences"
New-Item -ItemType Directory -Force -Path $preferencesDirectory, $webViewData | Out-Null
$preferences = [ordered]@{
    schema_version = 4; revision = 0; language = "system"; launch_at_startup = $false
    minimize_to_tray = $true; theme = "system"; reduced_motion = $false; compact_density = $false
    selected_profile_id = $null; voice_auto_play_chat = $false; voice_auto_play_pet = $false
    voice_volume_percent = 80; voice_rate_percent = 100; permission_cloud_profile = "standard"
    memory_enabled = $true; memory_retention_days = 90; analytics_enabled = $false
    pet_enabled = $true; pet_always_on_top = $true; pet_muted = $true; pet_size_percent = 100
    pet_opacity_percent = 92; pet_motion_enabled = $true; pet_particles_enabled = $true
    pet_hover_enabled = $true; pet_hover_dwell_ms = 250; pet_do_not_disturb = $false
    pet_remember_position = $true; pet_renderer_mode = "auto"; pet_optics_mode = "enhanced"
    pet_target_fps = $TargetFps; pet_anchor = $null; developer_mode = $false
}
[System.IO.File]::WriteAllText(
    (Join-Path $preferencesDirectory "desktop.json"),
    ($preferences | ConvertTo-Json -Depth 4),
    [System.Text.UTF8Encoding]::new($false)
)

$port = Get-AvailablePort
$process = $null
$viteProcess = $null
$ownedJob = $null
$cadenceResult = $null
$restartResult = $null
$liveBackdropResult = $null
$probe = Join-Path $root "desktop\scripts\probe-presence-native-gpu.mjs"
try {
    Normalize-ProcessPathEnvironment
    if (-not $KeepRunning) {
        $ownedJob = [FairyOwnedProcessJob]::new()
    }
    if (-not (Test-LoopbackPort 1430)) {
        # Use the same Vite development mode as `npm run dev`, without an npm wrapper
        # process that could leave an orphaned Node child after the native smoke test.
        $viteProcess = Start-ViteDevelopmentServer
        if ($null -ne $ownedJob) { $ownedJob.Add($viteProcess) }
    }
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = (Resolve-Path -LiteralPath $Executable).Path
    $startInfo.WorkingDirectory = Split-Path -Parent $startInfo.FileName
    $startInfo.EnvironmentVariables["FAIRY_DESKTOP_DATA_DIR"] = $fairyData
    $startInfo.EnvironmentVariables["WEBVIEW2_USER_DATA_FOLDER"] = $webViewData
    # Some managed CI/Codex tokens cannot create WebView2 sandbox child processes.
    # This opt-in applies only to the disposable, credential-free smoke profile.
    $webViewArguments = "--remote-debugging-port=$port"
    if ($RestrictedWebViewRunner) { $webViewArguments += " --no-sandbox" }
    $startInfo.EnvironmentVariables["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = $webViewArguments
    $startInfo.EnvironmentVariables["FAIRY_CORE_ROOT"] = (Resolve-Path -LiteralPath (Join-Path $root "core")).Path
    if ($DiagnosticSolid) {
        $startInfo.EnvironmentVariables["FAIRY_PRESENCE_GPU_DIAGNOSTIC_SOLID"] = "1"
    }
    $startInfo.UseShellExecute = $false
    $process = [System.Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) { throw "Fairy process did not start" }
    if ($null -ne $ownedJob) { $ownedJob.Add($process) }
    # The auxiliary HWND titles are intentionally empty so Windows cannot flash a title or
    # stale non-client pixels while the transparent group moves. CDP proves both pages loaded;
    # physical geometry then identifies their top-level WebView windows without restoring titles.
    $readyRaw = & node $probe --port $port --action status --target-fps $TargetFps --duration-seconds 0
    if ($LASTEXITCODE -ne 0) { throw "Presence WebViews did not become ready" }
    $render = Wait-FairyPresenceWebView $process "pet-render" 640 260
    # The input WebView owns the complete expandable interaction surface. Its native
    # hit region is reconciled separately, so identify the HWND from the maximum
    # logical surface instead of the obsolete 144px compact-window geometry.
    $input = Wait-FairyPresenceWebView $process "pet-input" 616 360
    # HWND registration precedes placement reconciliation by a few scheduler turns.
    Start-Sleep -Milliseconds 300
    $raw = & node $probe --port $port --action run --target-fps $TargetFps --duration-seconds $DurationSeconds
    if ($LASTEXITCODE -ne 0) { throw "Native GPU probe failed" }
    $result = ($raw -join "`n") | ConvertFrom-Json
    $status = $result.status
    $attemptPath = [System.IO.Path]::ChangeExtension($ScreenshotPath, ".attempt.json")
    [System.IO.File]::WriteAllText(
        $attemptPath,
        ($status | ConvertTo-Json -Depth 8),
        [System.Text.UTF8Encoding]::new($false)
    )
    if ($status.lifecycle -ne "running" -or
        $status.backend -ne "windows_dda_d3d11_composition" -or
        $status.optics_source -ne "desktop_duplication" -or
        -not $status.host_backdrop_composition -or
        -not $status.backdrop_pixel_access -or
        -not $status.continuous_displacement_supported -or
        $status.pixel_ipc -or
        $status.dda_exclusion -ne "applied" -or
        $status.monitor_handoff -ne "ready" -or
        [string]::IsNullOrWhiteSpace([string]$status.source_format) -or
        [string]::IsNullOrWhiteSpace([string]$status.adapter_luid)) {
        throw "Native GPU contract failed: $($status | ConvertTo-Json -Compress)"
    }
    if ([int]$status.target_frame_rate -ne $TargetFps) {
        throw "Native GPU renderer reported the wrong target frame rate: $($status.target_frame_rate)"
    }
    $effectiveFps = [Math]::Max(1, [int]$status.effective_frame_rate)
    $minimumFrames = [Math]::Max(1, [Math]::Floor($DurationSeconds * $effectiveFps * 0.75))
    if ([long]$status.frames_presented -lt $minimumFrames) {
        throw "Native GPU renderer presented too few frames: $($status.frames_presented)"
    }
    if ([double]$status.present_fps_avg -lt ($effectiveFps * 0.90)) {
        throw "Native GPU average frame rate missed the 90% gate: $($status.present_fps_avg)"
    }
    # DWM can quantize roughly one percent of 300 Hz presents into a 1.5-frame interval even
    # while the sustained cadence remains on target. Keep that tail visible in the report, but
    # judge GPU work by its dedicated p95 budget instead of forcing CPU catch-up bursts.
    $p1Ratio = if ($effectiveFps -ge 240) { 0.60 } else { 0.70 }
    if ([double]$status.frame_interval_p1_fps -lt ($effectiveFps * $p1Ratio)) {
        throw "Native GPU P1 frame rate missed the $([Math]::Round($p1Ratio * 100))% gate: $($status.frame_interval_p1_fps)"
    }
    $presentBudgetMs = if ($effectiveFps -ge 240) {
        3.2
    }
    elseif ($effectiveFps -ge 120) {
        6.0
    }
    else {
        8.0
    }
    if ([double]$status.present_p95_ms -gt $presentBudgetMs) {
        throw "Native GPU p95 latency gate failed: present=$($status.present_p95_ms)ms budget=$presentBudgetMs ms"
    }
    if ([int]$status.display_refresh_rate_hz -gt 0 -and
        [double]$status.present_fps_avg -gt ([double]$status.display_refresh_rate_hz * 1.10 + 2.0)) {
        throw "Native GPU presentation exceeded the display clock: present=$($status.present_fps_avg) display=$($status.display_refresh_rate_hz)"
    }
    $captureBudgetMs = if ([int]$status.display_refresh_rate_hz -gt 0) {
        [Math]::Max(8.0, 1000.0 / [double]$status.display_refresh_rate_hz)
    }
    else {
        16.7
    }
    if ([double]$status.capture_to_present_p95_ms -gt $captureBudgetMs) {
        throw "DDA capture-to-present p95 gate failed: capture=$($status.capture_to_present_p95_ms)ms budget=$captureBudgetMs ms"
    }
    if ($VerifyLiveBackdrop) {
        Add-Type -AssemblyName System.Drawing
        $nativeSurface = Wait-FairyWindowClass $process "FairyNativePresenceRendererClass"
        $liveRed = [System.IO.Path]::ChangeExtension($ScreenshotPath, ".live-red.png")
        $liveGreen = [System.IO.Path]::ChangeExtension($ScreenshotPath, ".live-green.png")
        $backdrop = [FairyColorBackdrop]::new(
            $render.X,
            $render.Y,
            $render.Width,
            $render.Height
        )
        try {
            $backdrop.PlaceBehindGroup($render.Handle, $nativeSurface.Handle, $input.Handle)
            $backdrop.SetColor(144, 24, 32)
            Start-Sleep -Milliseconds 650
            & node $probe --port $port --action capture --target-fps $TargetFps --duration-seconds 0 --output $liveRed | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Live red backdrop capture failed" }
            $backdrop.SetColor(24, 144, 40)
            Start-Sleep -Milliseconds 650
            & node $probe --port $port --action capture --target-fps $TargetFps --duration-seconds 0 --output $liveGreen | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Live green backdrop capture failed" }

            $redImage = $null
            $greenImage = $null
            try {
                $redImage = [System.Drawing.Bitmap]::new($liveRed)
                $greenImage = [System.Drawing.Bitmap]::new($liveGreen)
                $scale = [double]$redImage.Width / 640.0
                $centerX = [int][Math]::Round(96.0 * $scale)
                $centerY = [int][Math]::Round(88.0 * $scale)
                $minimumRadius = 12.0 * $scale
                $maximumRadius = 52.0 * $scale
                $difference = 0.0
                $samples = 0
                for ($y = [Math]::Max(0, $centerY - [int][Math]::Ceiling($maximumRadius));
                    $y -le [Math]::Min($redImage.Height - 1, $centerY + [int][Math]::Ceiling($maximumRadius));
                    $y++) {
                    for ($x = [Math]::Max(0, $centerX - [int][Math]::Ceiling($maximumRadius));
                        $x -le [Math]::Min($redImage.Width - 1, $centerX + [int][Math]::Ceiling($maximumRadius));
                        $x++) {
                        $radius = [Math]::Sqrt(
                            [Math]::Pow($x - $centerX, 2) + [Math]::Pow($y - $centerY, 2)
                        )
                        if ($radius -lt $minimumRadius -or $radius -gt $maximumRadius) { continue }
                        $redPixel = $redImage.GetPixel($x, $y)
                        $greenPixel = $greenImage.GetPixel($x, $y)
                        $difference += (
                            [Math]::Abs([int]$redPixel.R - [int]$greenPixel.R) +
                            [Math]::Abs([int]$redPixel.G - [int]$greenPixel.G) +
                            [Math]::Abs([int]$redPixel.B - [int]$greenPixel.B)
                        ) / 3.0
                        $samples++
                    }
                }
                if ($samples -lt 100) { throw "Live backdrop sample area is too small" }
                $meanDifference = $difference / $samples
                if ($meanDifference -lt 18.0) {
                    throw "Stationary lens did not refresh the live monitor background: mean difference=$meanDifference"
                }
                $meanRadialLuminance = {
                    param([double]$Radius, [double]$Tolerance = 1.0)
                    $red = Measure-RadialLuminance $redImage $centerX $centerY $Radius $Tolerance
                    $green = Measure-RadialLuminance $greenImage $centerX $centerY $Radius $Tolerance
                    return ($red + $green) / 2.0
                }
                $centerLuminance = & $meanRadialLuminance 0.0 (3.0 * $scale)
                $innerBaseline = (
                    (& $meanRadialLuminance (16.0 * $scale)) +
                    (& $meanRadialLuminance (30.0 * $scale))
                ) / 2.0
                $innerRingLuminance = & $meanRadialLuminance (24.0 * $scale)
                $outerBaseline = (
                    (& $meanRadialLuminance (30.0 * $scale)) +
                    (& $meanRadialLuminance (44.0 * $scale))
                ) / 2.0
                $outerRingLuminance = & $meanRadialLuminance (36.0 * $scale)
                $centerContrast = $centerLuminance - $innerBaseline
                $innerRingContrast = $innerRingLuminance - $innerBaseline
                $outerRingContrast = $outerRingLuminance - $outerBaseline
                if ($centerContrast -lt 20.0 -or
                    $innerRingContrast -lt 2.0 -or
                    $outerRingContrast -lt 3.0) {
                    throw "Foreground identity layer is missing or optically attenuated: center=$centerContrast inner=$innerRingContrast outer=$outerRingContrast"
                }
                $liveBackdropResult = [ordered]@{
                    mean_rgb_difference = $meanDifference
                    samples = $samples
                    identity_center_contrast = $centerContrast
                    identity_inner_ring_contrast = $innerRingContrast
                    identity_outer_ring_contrast = $outerRingContrast
                    first_capture = [System.IO.Path]::GetFullPath($liveRed)
                    second_capture = [System.IO.Path]::GetFullPath($liveGreen)
                }
            }
            finally {
                if ($null -ne $redImage) { $redImage.Dispose() }
                if ($null -ne $greenImage) { $greenImage.Dispose() }
            }
        }
        finally {
            $backdrop.Dispose()
        }
    }
    if ($VerifyCadence) {
        if ($TargetFps -ne 300) { throw "Cadence verification requires -TargetFps 300" }
        $cadenceRaw = & node $probe --port $port --action cadence --target-fps $TargetFps --duration-seconds 0
        if ($LASTEXITCODE -ne 0) { throw "Native GPU cadence probe failed" }
        $cadenceResult = ($cadenceRaw -join "`n") | ConvertFrom-Json
        foreach ($sample in $cadenceResult.samples) {
            $limit = [int]$sample.frame_rate_limit
            $effective = [Math]::Max(1, [int]$sample.effective_frame_rate)
            $displayRefresh = [Math]::Max(1, [double]$sample.display_refresh_rate_hz)
            $observed = [double]$sample.observed_fps
            if ($sample.lifecycle -ne "running" -or
                $observed -lt ($effective * 0.80) -or
                $observed -gt ($displayRefresh * 1.10 + 2.0)) {
                throw "Native GPU cadence gate failed at ${limit} FPS (effective ${effective} FPS, display ${displayRefresh} Hz): $observed"
            }
        }
    }
    if ($RestartCycles -gt 0) {
        & node $probe --port $port --action stop --target-fps $TargetFps --duration-seconds 0 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Native GPU lifecycle pre-stop failed" }
        $restartRaw = & node $probe --port $port --action restart --target-fps $TargetFps --duration-seconds 0 --cycles $RestartCycles
        if ($LASTEXITCODE -ne 0) { throw "Native GPU restart probe failed" }
        $restartResult = ($restartRaw -join "`n") | ConvertFrom-Json
        if ($restartResult.runs.Count -ne $RestartCycles) {
            throw "Native GPU restart probe returned the wrong cycle count"
        }
        foreach ($run in $restartResult.runs) {
            if ($run.running.lifecycle -ne "running" -or
                $run.running.backend -ne "windows_dda_d3d11_composition" -or
                $run.running.optics_source -ne "desktop_duplication" -or
                -not $run.running.host_backdrop_composition -or
                -not $run.running.backdrop_pixel_access -or
                -not $run.running.continuous_displacement_supported -or
                $run.running.pixel_ipc -or
                $run.running.dda_exclusion -ne "applied" -or
                $run.running.monitor_handoff -ne "ready" -or
                $run.stopped.lifecycle -ne "idle") {
                throw "Native GPU restart contract failed: $($run | ConvertTo-Json -Compress)"
            }
        }
        & node $probe --port $port --action run --target-fps $TargetFps --duration-seconds 1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Native GPU post-lifecycle restart failed" }
    }
    & node $probe --port $port --action prepare-capture --target-fps $TargetFps --duration-seconds 0 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Native GPU visual capture preparation failed" }
    # prepare_visual_test changes capture affinity and halts presentation asynchronously. Wait for
    # the HWND group and DPI virtualization to settle before asking the WebView-owned screenshot
    # command for its physical frame; otherwise a transient pre-move rectangle can fall outside
    # the monitor selected from its post-move center.
    Start-Sleep -Milliseconds 450
    $webViewRender = Wait-FairyPresenceWebView $process "pet-render" 640 260
    $render = Wait-FairyWindowClass $process "FairyNativePresenceRendererClass"
    if (-not [string]::IsNullOrEmpty($render.Title)) {
        throw "Native render surface unexpectedly exposes a window title: $($render.Title)"
    }
    $input = Wait-FairyPresenceWebView $process "pet-input" 616 360
    $surfaceAligned =
        $render.X -eq $webViewRender.X -and
        $render.Y -eq $webViewRender.Y -and
        $render.Width -eq $webViewRender.Width -and
        $render.Height -eq $webViewRender.Height
    if (-not $surfaceAligned) {
        throw "Native render surface is not aligned with the WebView render window"
    }
    $windowScale = [double]$webViewRender.Width / 640.0
    $expectedInputWidth = [int][Math]::Round(616.0 * $windowScale)
    $expectedInputHeight = [int][Math]::Round(360.0 * $windowScale)
    $coreInsetX = [int][Math]::Round(24.0 * $windowScale)
    $coreInsetY = [int][Math]::Round(16.0 * $windowScale)
    $inputCoreTop = [int][Math]::Round(116.0 * $windowScale)
    $inputCoreY = $webViewRender.Y + $coreInsetY - $inputCoreTop
    $inputCoreRightX = $webViewRender.X + $coreInsetX
    $inputCoreLeftX = $webViewRender.X + $webViewRender.Width - $coreInsetX - $expectedInputWidth
    $inputAligned =
        $input.Width -eq $expectedInputWidth -and
        $input.Height -eq $expectedInputHeight -and
        $input.Y -eq $inputCoreY -and
        ($input.X -eq $inputCoreRightX -or $input.X -eq $inputCoreLeftX)
    if (-not $inputAligned) {
        throw "Pet input core proxy is not aligned with the render window"
    }

    $coreCenterX = if ($input.X -eq $inputCoreRightX) {
        $webViewRender.X + [int][Math]::Round(96.0 * $windowScale)
    }
    else {
        $webViewRender.X + $webViewRender.Width - [int][Math]::Round(96.0 * $windowScale)
    }
    $coreCenterY = $webViewRender.Y + [int][Math]::Round(130.0 * $windowScale)
    $coreRadius = [double][Math]::Round(72.0 * $windowScale)
    $overlayHandles = @(
        $webViewRender.Handle.ToInt64(),
        $render.Handle.ToInt64(),
        $input.Handle.ToInt64()
    )
    $hitTestGrid = [System.Collections.Generic.List[object]]::new()
    foreach ($xFraction in @(0.05, 0.2, 0.35, 0.5, 0.65, 0.8, 0.95)) {
        foreach ($yFraction in @(0.08, 0.29, 0.5, 0.71, 0.92)) {
            $probeX = $webViewRender.X + [int][Math]::Round($webViewRender.Width * $xFraction)
            $probeY = $webViewRender.Y + [int][Math]::Round($webViewRender.Height * $yFraction)
            $distance = [Math]::Sqrt(
                [Math]::Pow($probeX - $coreCenterX, 2) +
                [Math]::Pow($probeY - $coreCenterY, 2)
            )
            if ($distance -le ($coreRadius + 4.0 * $windowScale)) { continue }
            $rootAtPoint = [FairyNativeGpuProbe]::RootWindowAt($probeX, $probeY)
            $blocked = $overlayHandles -contains $rootAtPoint.ToInt64()
            $hitTestGrid.Add([PSCustomObject]@{
                x = $probeX
                y = $probeY
                root_hwnd = "0x$([Convert]::ToString($rootAtPoint.ToInt64(), 16))"
                blocked = $blocked
            })
            if ($blocked) {
                throw "Fairy blocked an out-of-core hit-test grid point: ${probeX},${probeY}"
            }
        }
    }
    if ($hitTestGrid.Count -lt 20) {
        throw "Presence hit-test grid did not cover enough out-of-core points: $($hitTestGrid.Count)"
    }

    $processTree = @(Get-FairyProcessTree $process.Id)
    $voiceWorkers = @($processTree | Where-Object {
        [string]$_.CommandLine -match 'fairy_voice_worker|fairy-voice-worker|CosyVoice'
    })
    if ($voiceWorkers.Count -gt 0) {
        throw "Voice worker started during passive Fairy startup: $($voiceWorkers.CommandLine -join '; ')"
    }
    $processTreeWorkingSet = [long](($processTree |
        Measure-Object -Property WorkingSetSize -Sum).Sum)
    if ($processTreeWorkingSet -gt 1.5GB) {
        throw "Passive Fairy process tree exceeded the 1.5 GiB working-set gate: $processTreeWorkingSet bytes"
    }

    & node $probe --port $port --action capture --target-fps $TargetFps --duration-seconds 0 --output $ScreenshotPath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Native GPU diagnostic screenshot failed" }
    if (-not (Test-Path -LiteralPath $ScreenshotPath -PathType Leaf) -or
        (Get-Item -LiteralPath $ScreenshotPath).Length -lt 1KB) {
        throw "Native GPU diagnostic screenshot is missing or empty"
    }
    $transparentHitTest = [FairyNativeGpuProbe]::ReturnsTransparentHitTest($render)
    if (-not $transparentHitTest) { throw "pet-render did not preserve native HTTRANSPARENT" }
    $passThrough = $null
    if (-not $SkipClickProbe) {
        $clickX = $render.X + [Math]::Floor($render.Width / 2)
        $clickY = $render.Y + $render.Height - 24
        $windowAtClickBefore = [FairyNativeGpuProbe]::WindowAt($clickX, $clickY)
        $sentinel = [FairyClickSentinel]::new($clickX - 24, $clickY - 24, 48, 48)
        $sentinelHandle = $sentinel.Handle
        $sentinelFrame = $sentinel.Frame()
        $windowAtClickProbe = [IntPtr]::Zero
        try {
            # Place the probe directly below the lowest Fairy overlay HWND. A no-activate popup is
            # otherwise allowed to remain behind the user's current foreground application, which
            # would make a successful Fairy pass-through look like a failed sentinel click.
            $sentinel.PlaceBehindGroup($webViewRender.Handle, $render.Handle, $input.Handle)
            $windowAtClickProbe = [FairyNativeGpuProbe]::WindowAt($clickX, $clickY)
            $passThrough = $sentinel.ClickAndWait($clickX, $clickY, 1500)
        }
        finally {
            $sentinel.Dispose()
        }
        if (-not $passThrough) {
            $windowAtClickAfter = [FairyNativeGpuProbe]::WindowAt($clickX, $clickY)
            throw "pet-render blocked a real pointer click outside the Fairy input region; point=${clickX},${clickY}; sentinel=0x$([Convert]::ToString($sentinelHandle.ToInt64(), 16)) frame=${sentinelFrame}; live_probe=0x$([Convert]::ToString($windowAtClickProbe.ToInt64(), 16)); before=0x$([Convert]::ToString($windowAtClickBefore.ToInt64(), 16)); after=0x$([Convert]::ToString($windowAtClickAfter.ToInt64(), 16)); windows=$(Format-FairyProcessWindows $process)"
        }
    }
    $report = [ordered]@{
        generated_at = [DateTime]::UtcNow.ToString("o")
        process_id = $process.Id
        webview2_port = $port
        target_fps = $TargetFps
        duration_seconds = $DurationSeconds
        status = $status
        webview_render_window = $webViewRender
        render_window = $render
        input_window = $input
        window_group_aligned = $surfaceAligned -and $inputAligned
        transparent_hit_test = $transparentHitTest
        hit_test_grid = $hitTestGrid
        click_through = $passThrough
        passive_process_tree_working_set_bytes = $processTreeWorkingSet
        passive_voice_worker_count = $voiceWorkers.Count
        cadence = $cadenceResult
        restart_cycles = $restartResult
        live_backdrop = $liveBackdropResult
        screenshot = [System.IO.Path]::GetFullPath($ScreenshotPath)
    }
    $outputDirectory = Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath))
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8
    $report | ConvertTo-Json -Depth 8
    if (-not $KeepRunning) {
        & node $probe --port $port --action stop --target-fps $TargetFps --duration-seconds 0 | Out-Null
    }
}
finally {
    if (-not $KeepRunning -and $null -ne $ownedJob) {
        $ownedJob.Dispose()
        $ownedJob = $null
    }
    if (-not $KeepRunning -and $null -ne $process) { Stop-ProcessTree $process }
    if (-not $KeepRunning -and $null -ne $viteProcess) { Stop-ProcessTree $viteProcess }
    if (-not $KeepRunning) {
        Start-Sleep -Milliseconds 300
        Remove-VerifiedScratch $scratch
    }
}
