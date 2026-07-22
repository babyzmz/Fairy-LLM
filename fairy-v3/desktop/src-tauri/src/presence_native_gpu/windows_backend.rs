use std::cmp::Ordering;
use std::collections::VecDeque;
use std::ffi::c_void;
use std::sync::atomic::{AtomicBool, AtomicIsize, AtomicU64, Ordering as AtomicOrdering};
use std::sync::mpsc;
use std::sync::{Arc, Condvar, Mutex, OnceLock, RwLock};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use super::{
    NativeGpuBackend, NativeGpuConfig, NativeGpuError, NativeGpuExpansionDirection,
    NativeGpuLifecycle, NativeGpuOpticsSource, NativeGpuPresentation, NativeGpuStatus,
    NativeGpuVisualState,
};
use crate::presence_coordinator::PhysicalFrame;
use windows::core::{w, Interface, PCSTR, PCWSTR};
use windows::System::DispatcherQueueController;
use windows::Win32::Foundation::{HINSTANCE, HMODULE, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows::Win32::Graphics::Direct3D::Fxc::D3DCompile;
use windows::Win32::Graphics::Direct3D::{
    ID3DBlob, ID3DInclude, D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST, D3D_DRIVER_TYPE_HARDWARE,
    D3D_FEATURE_LEVEL, D3D_FEATURE_LEVEL_10_0, D3D_FEATURE_LEVEL_10_1, D3D_FEATURE_LEVEL_11_0,
    D3D_FEATURE_LEVEL_11_1,
};
use windows::Win32::Graphics::Direct3D11::{
    D3D11CreateDevice, ID3D11Buffer, ID3D11Device, ID3D11DeviceContext, ID3D11PixelShader,
    ID3D11RenderTargetView, ID3D11SamplerState, ID3D11ShaderResourceView, ID3D11Texture2D,
    ID3D11VertexShader, D3D11_BIND_CONSTANT_BUFFER, D3D11_BIND_SHADER_RESOURCE, D3D11_BUFFER_DESC,
    D3D11_COMPARISON_NEVER, D3D11_CREATE_DEVICE_BGRA_SUPPORT, D3D11_FILTER_MIN_MAG_MIP_LINEAR,
    D3D11_SAMPLER_DESC, D3D11_SDK_VERSION, D3D11_TEXTURE2D_DESC, D3D11_TEXTURE_ADDRESS_CLAMP,
    D3D11_USAGE_DEFAULT, D3D11_VIEWPORT,
};
use windows::Win32::Graphics::Dwm::{DwmSetWindowAttribute, DWMWA_USE_HOSTBACKDROPBRUSH};
use windows::Win32::Graphics::Dxgi::Common::{
    DXGI_ALPHA_MODE_PREMULTIPLIED, DXGI_COLOR_SPACE_RGB_FULL_G10_NONE_P709,
    DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_SAMPLE_DESC,
};
use windows::Win32::Graphics::Dxgi::{
    CreateDXGIFactory1, IDXGIDevice, IDXGIFactory1, IDXGIFactory2, IDXGIOutput1, IDXGIOutput6,
    IDXGIOutputDuplication, IDXGIResource, IDXGISwapChain1, IDXGISwapChain3, DXGI_ERROR_NOT_FOUND,
    DXGI_ERROR_WAIT_TIMEOUT, DXGI_OUTDUPL_FRAME_INFO, DXGI_PRESENT, DXGI_SCALING_STRETCH,
    DXGI_SWAP_CHAIN_DESC1, DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL, DXGI_USAGE_RENDER_TARGET_OUTPUT,
};
use windows::Win32::Graphics::Gdi::{
    EnumDisplaySettingsW, GetMonitorInfoW, MonitorFromPoint, DEVMODEW, ENUM_CURRENT_SETTINGS,
    HMONITOR, MONITORINFO, MONITORINFOEXW, MONITOR_DEFAULTTONEAREST,
};
use windows::Win32::System::Com::{CoDecrementMTAUsage, CoIncrementMTAUsage, CO_MTA_USAGE_COOKIE};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Threading::{
    GetCurrentThread, GetCurrentThreadId, SetThreadPriority, THREAD_PRIORITY_ABOVE_NORMAL,
};
use windows::Win32::System::WinRT::Composition::{ICompositorDesktopInterop, ICompositorInterop};
use windows::Win32::System::WinRT::{
    CreateDispatcherQueueController, DispatcherQueueOptions, RoInitialize, RoUninitialize,
    DQTAT_COM_NONE, DQTYPE_THREAD_CURRENT, RO_INIT_MULTITHREADED,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW, GetWindowRect, IsWindow,
    PeekMessageW, PostMessageW, RegisterClassW, SetWindowPos, ShowWindow, TranslateMessage,
    HTTRANSPARENT, HWND_NOTOPMOST, HWND_TOPMOST, MSG, PBT_APMSUSPEND, PM_REMOVE, SWP_NOACTIVATE,
    SWP_NOCOPYBITS, SWP_NOMOVE, SWP_NOOWNERZORDER, SWP_NOSIZE, SW_SHOWNOACTIVATE, WM_CLOSE,
    WM_DISPLAYCHANGE, WM_DPICHANGED, WM_NCHITTEST, WM_POWERBROADCAST, WM_QUIT, WNDCLASSW,
    WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_NOREDIRECTIONBITMAP, WS_EX_TOOLWINDOW, WS_EX_TOPMOST,
    WS_EX_TRANSPARENT, WS_POPUP,
};
use windows::UI::Composition::Desktop::DesktopWindowTarget;
use windows::UI::Composition::{
    CompositionBackdropBrush, CompositionEllipseGeometry, CompositionGeometricClip,
    CompositionRoundedRectangleGeometry, CompositionStretch, CompositionSurfaceBrush, Compositor,
    ContainerVisual, SpriteVisual,
};
use windows_numerics::{Vector2, Vector3};

const SHADER_SOURCE: &str = include_str!("liquid_glass.hlsl");
const SWAP_CHAIN_BUFFER_COUNT: u32 = 2;
const LIQUID_CORE_RADIUS: f32 = 72.0;
const EDGE_CAPTURE_REBASE_FRAMES: u64 = 2;
const METRIC_WINDOW: usize = 600;
const NATIVE_SURFACE_CLASS: PCWSTR = w!("FairyNativePresenceRendererClass");
const TIMER_PERIOD_ONE_MILLISECOND: u32 = 1;
const TIMER_NO_ERROR: u32 = 0;
static NATIVE_SURFACE_CLASS_REGISTERED: OnceLock<Result<(), String>> = OnceLock::new();

#[link(name = "winmm")]
unsafe extern "system" {
    #[link_name = "timeBeginPeriod"]
    fn time_begin_period(period_ms: u32) -> u32;
    #[link_name = "timeEndPeriod"]
    fn time_end_period(period_ms: u32) -> u32;
}

pub(super) struct WindowsNativeGpuSession {
    control: NativeRenderLoop,
    surface_hwnd: Arc<AtomicIsize>,
    presentation: Arc<RwLock<NativeGpuPresentation>>,
    render_control: Arc<NativeRenderControl>,
    status: Arc<Mutex<NativeGpuStatus>>,
    _timer_resolution: TimerResolutionGuard,
}

struct TimerResolutionGuard {
    active: bool,
}

impl TimerResolutionGuard {
    fn acquire() -> Self {
        Self {
            active: unsafe { time_begin_period(TIMER_PERIOD_ONE_MILLISECOND) } == TIMER_NO_ERROR,
        }
    }
}

impl Drop for TimerResolutionGuard {
    fn drop(&mut self) {
        if self.active {
            unsafe {
                time_end_period(TIMER_PERIOD_ONE_MILLISECOND);
            }
        }
    }
}

impl WindowsNativeGpuSession {
    pub(super) fn start(
        config: NativeGpuConfig,
        status: Arc<Mutex<NativeGpuStatus>>,
    ) -> Result<Self, NativeGpuError> {
        let timer_resolution = TimerResolutionGuard::acquire();
        let composition_source =
            inspect_desktop_composition_source(config.render_frame, config.presentation, &status)?;
        let monitor_frame = composition_source.monitor_frame;
        let display_refresh_rate_hz = composition_source.display_refresh_rate_hz;
        let hdr_capture = composition_source.hdr_capture;
        let effective_frame_rate =
            effective_render_frame_rate(config.target_frame_rate, display_refresh_rate_hz);
        update_status(&status, |status| {
            status.hdr_capture = hdr_capture;
            status.display_refresh_rate_hz = display_refresh_rate_hz;
            // Legacy diagnostic field retained for schema compatibility. HostBackdrop does not
            // run a capture loop, so its only meaningful limit is the foreground present rate.
            status.capture_frame_rate_limit = effective_frame_rate;
        });
        let (device, context) = create_native_d3d_device(&status)?;
        let surface_hwnd = Arc::new(AtomicIsize::new(0));
        let presentation = Arc::new(RwLock::new(config.presentation));
        let render_control = Arc::new(NativeRenderControl::new());
        let control = NativeRenderLoop::start(
            device,
            context,
            config,
            monitor_frame,
            display_refresh_rate_hz,
            Arc::clone(&surface_hwnd),
            Arc::clone(&presentation),
            Arc::clone(&status),
            hdr_capture,
            Arc::clone(&render_control),
        )
        .map_err(|error| {
            composition_source_failure(
                &status,
                "renderer_start",
                format!("PRESENCE_NATIVE_GPU_RENDERER_START_FAILED: {error}"),
            )
        })?;
        update_status(&status, |status| {
            status.capture_source_stage = "host_backdrop_renderer_started".to_owned();
            status.capture_source_hresult = None;
            status.optics_source = NativeGpuOpticsSource::HostBackdrop;
        });
        Ok(Self {
            control,
            surface_hwnd,
            presentation,
            render_control,
            status,
            _timer_resolution: timer_resolution,
        })
    }

    pub(super) fn status(&self) -> NativeGpuStatus {
        self.status
            .lock()
            .map(|status| status.clone())
            .unwrap_or_else(|_| NativeGpuStatus {
                lifecycle: NativeGpuLifecycle::Failed,
                error_code: Some("PRESENCE_NATIVE_GPU_STATUS_LOCK_FAILED".to_owned()),
                ..NativeGpuStatus::default()
            })
    }

    pub(super) fn wait_for_first_present(&self) -> Result<NativeGpuStatus, NativeGpuError> {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            let status = self.status();
            match status.lifecycle {
                NativeGpuLifecycle::Running if status.frames_presented >= 2 => return Ok(status),
                NativeGpuLifecycle::Failed => {
                    return Err(NativeGpuError::StartFailed(
                        status
                            .error_code
                            .unwrap_or_else(|| "renderer initialization failed".to_owned()),
                    ));
                }
                _ if Instant::now() >= deadline => return Err(NativeGpuError::StartupTimeout),
                _ => thread::sleep(Duration::from_millis(2)),
            }
        }
    }

    pub(super) fn stop(mut self) -> Result<(), NativeGpuError> {
        let raw_hwnd = self.surface_hwnd.load(AtomicOrdering::Acquire);
        self.control.stop_and_join();
        if raw_hwnd != 0 {
            let hwnd = HWND(raw_hwnd as *mut c_void);
            let deadline = Instant::now() + Duration::from_millis(750);
            while unsafe { IsWindow(Some(hwnd)) }.as_bool() && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(5));
            }
        }
        Ok(())
    }

    pub(super) fn surface_handle(&self) -> Option<isize> {
        let raw_hwnd = self.surface_hwnd.load(AtomicOrdering::Acquire);
        if raw_hwnd == 0 {
            return None;
        }
        unsafe { IsWindow(Some(HWND(raw_hwnd as *mut c_void))) }
            .as_bool()
            .then_some(raw_hwnd)
    }

    pub(super) fn prepare_visual_test(&self) -> Result<(), NativeGpuError> {
        let raw_hwnd = self.surface_hwnd.load(AtomicOrdering::Acquire);
        if raw_hwnd == 0 {
            return Err(NativeGpuError::NotRunning);
        }
        crate::presence_backdrop::set_window_capture_excluded(raw_hwnd, false)
            .map_err(NativeGpuError::StartFailed)
    }

    pub(super) fn update(&self, presentation: NativeGpuPresentation) -> Result<(), NativeGpuError> {
        *self.presentation.write().map_err(|_| {
            NativeGpuError::StartFailed("presentation lock unavailable".to_owned())
        })? = presentation;
        if let Ok(mut status) = self.status.lock() {
            status.presentation_revision = status.presentation_revision.saturating_add(1);
        }
        Ok(())
    }

    pub(super) fn presentation(&self) -> Option<NativeGpuPresentation> {
        self.presentation.read().ok().map(|value| *value)
    }

    pub(super) fn set_always_on_top(&self, always_on_top: bool) -> Result<(), NativeGpuError> {
        let raw_hwnd = self.surface_hwnd.load(AtomicOrdering::Acquire);
        if raw_hwnd == 0 {
            // A failed or rebuilding renderer must not make an already-persisted preference
            // update fail. The next start reads the same value into NativeGpuConfig.
            return Ok(());
        }
        set_surface_always_on_top(HWND(raw_hwnd as *mut c_void), always_on_top)
            .map_err(NativeGpuError::StartFailed)
    }

    pub(super) fn set_drag_active(&self, active: bool) -> Result<(), NativeGpuError> {
        self.render_control
            .set_drag_active(active)
            .map_err(NativeGpuError::StartFailed)
    }

    pub(super) fn is_drag_active(&self) -> bool {
        self.render_control.is_drag_active()
    }
}

struct NativeRenderControl {
    drag_active: AtomicBool,
    rebase_epoch: AtomicU64,
    drag_request_epoch: AtomicU64,
    drag_ack_epoch: AtomicU64,
    wake: Condvar,
}

impl NativeRenderControl {
    fn new() -> Self {
        Self {
            drag_active: AtomicBool::new(false),
            rebase_epoch: AtomicU64::new(0),
            drag_request_epoch: AtomicU64::new(0),
            drag_ack_epoch: AtomicU64::new(0),
            wake: Condvar::new(),
        }
    }

    fn set_drag_active(&self, active: bool) -> Result<(), String> {
        let request = self
            .drag_request_epoch
            .fetch_add(1, AtomicOrdering::AcqRel)
            .saturating_add(1);
        if active {
            self.drag_active.store(true, AtomicOrdering::Release);
        } else {
            self.rebase_epoch.fetch_add(1, AtomicOrdering::AcqRel);
            self.drag_active.store(false, AtomicOrdering::Release);
        }
        self.wake.notify_all();
        let deadline = Instant::now() + Duration::from_millis(75);
        while self.drag_ack_epoch.load(AtomicOrdering::Acquire) < request {
            if Instant::now() >= deadline {
                return Err("PRESENCE_NATIVE_GPU_DRAG_MODE_TIMEOUT".to_owned());
            }
            thread::sleep(Duration::from_millis(1));
        }
        Ok(())
    }

    fn acknowledge_drag_mode(&self) {
        let request = self.drag_request_epoch.load(AtomicOrdering::Acquire);
        self.drag_ack_epoch.store(request, AtomicOrdering::Release);
    }

    fn is_drag_active(&self) -> bool {
        self.drag_active.load(AtomicOrdering::Acquire)
    }
}

struct NativeRenderShared {
    wake_lock: Mutex<()>,
    stopping: AtomicBool,
}

struct NativeRenderLoop {
    shared: Arc<NativeRenderShared>,
    control: Arc<NativeRenderControl>,
    thread: Option<JoinHandle<()>>,
}

impl NativeRenderLoop {
    #[allow(clippy::too_many_arguments)]
    fn start(
        device: ID3D11Device,
        context: ID3D11DeviceContext,
        config: NativeGpuConfig,
        monitor_frame: PhysicalFrame,
        display_refresh_rate_hz: u16,
        surface_hwnd: Arc<AtomicIsize>,
        presentation: Arc<RwLock<NativeGpuPresentation>>,
        status: Arc<Mutex<NativeGpuStatus>>,
        hdr_capture: bool,
        control: Arc<NativeRenderControl>,
    ) -> Result<Self, String> {
        let shared = Arc::new(NativeRenderShared {
            wake_lock: Mutex::new(()),
            stopping: AtomicBool::new(false),
        });
        let thread_shared = Arc::clone(&shared);
        let thread_control = Arc::clone(&control);
        let thread_status = Arc::clone(&status);
        let (startup_tx, startup_rx) = mpsc::sync_channel(1);
        let render_thread = thread::Builder::new()
            .name("fairy-presence-render".to_owned())
            .spawn(move || {
                let _ =
                    unsafe { SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_ABOVE_NORMAL) };
                let _winrt = match CompositionWinRtMta::acquire(&thread_status) {
                    Ok(guard) => guard,
                    Err(error) => {
                        let error =
                            format!("PRESENCE_NATIVE_GPU_COMPOSITION_WINRT_FAILED: {error}");
                        let _ = startup_tx.send(Err(error.clone()));
                        update_status(&thread_status, |status| {
                            status.lifecycle = NativeGpuLifecycle::Failed;
                            status.error_code = Some(error);
                        });
                        return;
                    }
                };
                let renderer = NativeCompositionRenderer::new(
                    device,
                    context,
                    NativeCompositionRendererInput {
                        config,
                        monitor_frame,
                        display_refresh_rate_hz,
                        surface_hwnd,
                        presentation,
                        hdr_capture,
                        status: Arc::clone(&thread_status),
                    },
                );
                let mut renderer = match renderer {
                    Ok(renderer) => renderer,
                    Err(error) => {
                        let _ = startup_tx.send(Err(error.clone()));
                        update_status(&thread_status, |status| {
                            status.lifecycle = NativeGpuLifecycle::Failed;
                            status.error_code = Some(error);
                        });
                        return;
                    }
                };
                update_status(&thread_status, |status| {
                    status.backend = NativeGpuBackend::WindowsHostBackdropD3d11Composition;
                    status.lifecycle = NativeGpuLifecycle::Running;
                    // HostBackdrop remains GPU-composed and never enters CPU memory or IPC. This
                    // legacy flag now describes that zero-copy compositor path, not WGC.
                    status.zero_copy_capture = true;
                    status.pixel_ipc = false;
                    status.monitor_width = monitor_frame.width;
                    status.monitor_height = monitor_frame.height;
                    status.started_at_ms = now_ms();
                    status.error_code = None;
                });
                let _ = startup_tx.send(Ok(()));
                run_render_loop(
                    &mut renderer,
                    &thread_shared,
                    &thread_control,
                    &thread_status,
                );
                renderer.surface.close_on_owner_thread();
            })
            .map_err(|error| format!("PRESENCE_NATIVE_GPU_RENDER_THREAD_FAILED: {error}"))?;
        let mut control = Self {
            shared,
            control,
            thread: Some(render_thread),
        };
        match startup_rx.recv_timeout(Duration::from_secs(2)) {
            Ok(Ok(())) => Ok(control),
            Ok(Err(error)) => {
                control.stop_and_join();
                Err(error)
            }
            Err(_) => {
                control.stop_and_join();
                Err("PRESENCE_NATIVE_GPU_RENDER_START_TIMEOUT".to_owned())
            }
        }
    }

    fn stop_and_join(&mut self) {
        self.shared.stopping.store(true, AtomicOrdering::Release);
        self.control.wake.notify_all();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

impl Drop for NativeRenderLoop {
    fn drop(&mut self) {
        self.stop_and_join();
    }
}

struct NativeRenderMetrics {
    started: Instant,
    last_presented: Option<Instant>,
    last_source_frame: Option<Instant>,
    last_source_generation: u64,
    frame_intervals_ms: VecDeque<f64>,
    source_frame_intervals_ms: VecDeque<f64>,
    present_ms: VecDeque<f64>,
    frames_presented: u64,
    last_published: Instant,
}

impl NativeRenderMetrics {
    fn new() -> Self {
        Self {
            started: Instant::now(),
            last_presented: None,
            last_source_frame: None,
            last_source_generation: 0,
            frame_intervals_ms: VecDeque::with_capacity(METRIC_WINDOW),
            source_frame_intervals_ms: VecDeque::with_capacity(METRIC_WINDOW),
            present_ms: VecDeque::with_capacity(METRIC_WINDOW),
            frames_presented: 0,
            last_published: Instant::now(),
        }
    }

    fn record(
        &mut self,
        source_generation: u64,
        present_started: Instant,
        presented: Instant,
        status: &Arc<Mutex<NativeGpuStatus>>,
    ) {
        if let Some(last) = self.last_presented.replace(present_started) {
            push_metric(
                &mut self.frame_intervals_ms,
                present_started.duration_since(last).as_secs_f64() * 1_000.0,
            );
        }
        push_metric(
            &mut self.present_ms,
            presented.duration_since(present_started).as_secs_f64() * 1_000.0,
        );
        if source_generation > self.last_source_generation {
            if let Some(last) = self.last_source_frame.replace(presented) {
                push_metric(
                    &mut self.source_frame_intervals_ms,
                    presented.duration_since(last).as_secs_f64() * 1_000.0,
                );
            }
            self.last_source_generation = source_generation;
        }
        self.frames_presented = self.frames_presented.saturating_add(1);
        if self.frames_presented <= 3 || self.last_published.elapsed() >= Duration::from_millis(250)
        {
            self.publish(status);
            self.last_published = presented;
        }
    }

    fn publish(&self, status: &Arc<Mutex<NativeGpuStatus>>) {
        let elapsed = self.started.elapsed().as_secs_f64();
        update_status(status, |status| {
            status.frames_presented = self.frames_presented;
            status.capture_fps_avg = if elapsed > 0.0 {
                self.frames_presented as f64 / elapsed
            } else {
                0.0
            };
            status.frame_interval_p1_fps = percentile(&self.frame_intervals_ms, 0.99)
                .filter(|interval| *interval > 0.0)
                .map(|interval| 1_000.0 / interval)
                .unwrap_or(0.0);
            status.source_frames_received = self.last_source_generation;
            status.source_capture_fps_avg = if elapsed > 0.0 {
                self.last_source_generation as f64 / elapsed
            } else {
                0.0
            };
            status.source_frame_interval_p1_fps = percentile(&self.source_frame_intervals_ms, 0.99)
                .filter(|interval| *interval > 0.0)
                .map(|interval| 1_000.0 / interval)
                .unwrap_or(0.0);
            status.callback_to_present_p95_ms = 0.0;
            status.present_p95_ms = percentile(&self.present_ms, 0.95).unwrap_or(0.0);
            status.last_presented_at_ms = now_ms();
        });
    }
}

fn run_render_loop(
    renderer: &mut NativeCompositionRenderer,
    shared: &NativeRenderShared,
    control: &NativeRenderControl,
    status: &Arc<Mutex<NativeGpuStatus>>,
) {
    let mut metrics = NativeRenderMetrics::new();
    let mut active_frame_rate = 0;
    let mut next_deadline = Instant::now();
    let mut applied_rebase_epoch = control.rebase_epoch.load(AtomicOrdering::Acquire);
    while !shared.stopping.load(AtomicOrdering::Acquire) && pump_native_messages() {
        let rebase_epoch = control.rebase_epoch.load(AtomicOrdering::Acquire);
        if rebase_epoch != applied_rebase_epoch {
            if let Err(error) = renderer.rebase_after_drag() {
                fail_render_loop(status, error);
                break;
            }
            applied_rebase_epoch = rebase_epoch;
            next_deadline = Instant::now();
        }
        control.acknowledge_drag_mode();
        let drag_active = control.is_drag_active();
        let frame_rate = match renderer.presentation_frame_rate() {
            Ok(frame_rate) => frame_rate,
            Err(error) => {
                fail_render_loop(status, error);
                break;
            }
        };
        let now = Instant::now();
        if frame_rate != active_frame_rate {
            active_frame_rate = frame_rate;
            next_deadline = now;
            update_status(status, |status| status.effective_frame_rate = frame_rate);
        }
        if now < next_deadline {
            wait_for_render_deadline(shared, control, next_deadline, frame_rate);
            continue;
        }
        let present_started = Instant::now();
        let source_generation = match renderer.present(metrics.started.elapsed(), drag_active) {
            Ok(generation) => generation,
            Err(error) => {
                fail_render_loop(status, error);
                break;
            }
        };
        let presented = Instant::now();
        metrics.record(source_generation, present_started, presented, status);
        next_deadline =
            advance_render_deadline(next_deadline, presented, render_interval(frame_rate));
    }
    metrics.publish(status);
}

fn wait_for_render_deadline(
    shared: &NativeRenderShared,
    control: &NativeRenderControl,
    deadline: Instant,
    frames_per_second: u16,
) {
    let spin_window = render_spin_window(frames_per_second);
    loop {
        if shared.stopping.load(AtomicOrdering::Acquire) {
            return;
        }
        let now = Instant::now();
        if now >= deadline {
            return;
        }
        let remaining = deadline.duration_since(now);
        if remaining <= spin_window {
            std::hint::spin_loop();
            continue;
        }
        let Ok(guard) = shared.wake_lock.lock() else {
            return;
        };
        let _ = control.wake.wait_timeout(guard, remaining - spin_window);
    }
}

fn pump_native_messages() -> bool {
    let mut message = MSG::default();
    while unsafe { PeekMessageW(&mut message, None, 0, 0, PM_REMOVE) }.as_bool() {
        if message.message == WM_QUIT {
            return false;
        }
        unsafe {
            let _ = TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }
    true
}

fn fail_render_loop(status: &Arc<Mutex<NativeGpuStatus>>, error: String) {
    update_status(status, |status| {
        status.lifecycle = NativeGpuLifecycle::Failed;
        status.error_code = Some(error);
    });
}

fn render_interval(frames_per_second: u16) -> Duration {
    Duration::from_secs_f64(1.0 / f64::from(frames_per_second))
}

fn render_spin_window(frames_per_second: u16) -> Duration {
    if frames_per_second >= 240 {
        Duration::from_micros(60)
    } else if frames_per_second >= 120 {
        Duration::from_micros(100)
    } else {
        Duration::from_micros(200)
    }
}

fn valid_display_refresh_rate(value: u32) -> Option<u16> {
    u16::try_from(value)
        .ok()
        .filter(|rate| (24..=1_000).contains(rate))
}

fn effective_render_frame_rate(requested: u16, display_refresh_rate_hz: u16) -> u16 {
    if (24..=1_000).contains(&display_refresh_rate_hz) {
        requested.min(display_refresh_rate_hz)
    } else {
        requested
    }
}

fn advance_render_deadline(
    mut deadline: Instant,
    presented: Instant,
    interval: Duration,
) -> Instant {
    deadline += interval;
    while deadline <= presented {
        deadline += interval;
    }
    deadline
}

struct NativeCompositionSurface {
    hwnd: HWND,
    tracking_hwnd: HWND,
    input_hwnd: HWND,
    frame: PhysicalFrame,
    shown: bool,
    creator_thread_id: u32,
    published_hwnd: Arc<AtomicIsize>,
}

impl NativeCompositionSurface {
    fn new(config: NativeGpuConfig, published_hwnd: Arc<AtomicIsize>) -> Result<Self, String> {
        register_native_surface_class()?;
        let module = unsafe { GetModuleHandleW(PCWSTR::null()) }
            .map_err(|error| windows_stage_error("WINDOW_MODULE", error))?;
        let width = i32::try_from(config.render_frame.width)
            .map_err(|_| "PRESENCE_NATIVE_GPU_SURFACE_SIZE_INVALID".to_owned())?;
        let height = i32::try_from(config.render_frame.height)
            .map_err(|_| "PRESENCE_NATIVE_GPU_SURFACE_SIZE_INVALID".to_owned())?;
        // HTTRANSPARENT only continues hit testing through windows owned by the same thread.
        // The DComp worker owns this top-level HWND while the interactive proxy belongs to
        // Tauri's UI thread, so Windows requires LAYERED + TRANSPARENT for global pass-through.
        let mut extended_style = WS_EX_LAYERED
            | WS_EX_NOACTIVATE
            | WS_EX_NOREDIRECTIONBITMAP
            | WS_EX_TOOLWINDOW
            | WS_EX_TRANSPARENT;
        if config.always_on_top {
            extended_style |= WS_EX_TOPMOST;
        }
        let hwnd = unsafe {
            CreateWindowExW(
                extended_style,
                NATIVE_SURFACE_CLASS,
                PCWSTR::null(),
                WS_POPUP,
                config.render_frame.x,
                config.render_frame.y,
                width,
                height,
                None,
                None,
                Some(HINSTANCE(module.0)),
                None,
            )
        }
        .map_err(|error| windows_stage_error("WINDOW_CREATE", error))?;
        if let Err(error) =
            crate::presence_backdrop::set_window_capture_excluded(hwnd.0 as isize, false)
        {
            let _ = unsafe { DestroyWindow(hwnd) };
            return Err(error);
        }
        published_hwnd.store(hwnd.0 as isize, AtomicOrdering::Release);
        Ok(Self {
            hwnd,
            tracking_hwnd: HWND(config.render_hwnd as *mut c_void),
            input_hwnd: HWND(config.input_hwnd as *mut c_void),
            frame: config.render_frame,
            shown: false,
            creator_thread_id: unsafe { GetCurrentThreadId() },
            published_hwnd,
        })
    }

    fn show(&mut self) -> Result<(), String> {
        if self.shown {
            return Ok(());
        }
        unsafe {
            SetWindowPos(
                self.hwnd,
                Some(self.input_hwnd),
                self.frame.x,
                self.frame.y,
                self.frame.width as i32,
                self.frame.height as i32,
                SWP_NOACTIVATE | SWP_NOCOPYBITS | SWP_NOOWNERZORDER,
            )
            .map_err(|error| windows_stage_error("WINDOW_POSITION", error))?;
            let _ = ShowWindow(self.hwnd, SW_SHOWNOACTIVATE);
        }
        self.shown = true;
        Ok(())
    }

    fn sync_to_tracking_window(&mut self) -> Result<PhysicalFrame, String> {
        let mut rectangle = RECT::default();
        unsafe { GetWindowRect(self.tracking_hwnd, &mut rectangle) }
            .map_err(|error| windows_stage_error("TRACKING_WINDOW_RECT", error))?;
        let width = u32::try_from(rectangle.right.saturating_sub(rectangle.left))
            .map_err(|_| "PRESENCE_NATIVE_GPU_TRACKING_FRAME_INVALID".to_owned())?;
        let height = u32::try_from(rectangle.bottom.saturating_sub(rectangle.top))
            .map_err(|_| "PRESENCE_NATIVE_GPU_TRACKING_FRAME_INVALID".to_owned())?;
        if width != self.frame.width || height != self.frame.height {
            return Err("PRESENCE_NATIVE_GPU_SURFACE_RESIZE_REQUIRED".to_owned());
        }
        let next = PhysicalFrame {
            x: rectangle.left,
            y: rectangle.top,
            width,
            height,
        };
        if next != self.frame {
            unsafe {
                SetWindowPos(
                    self.hwnd,
                    Some(self.input_hwnd),
                    next.x,
                    next.y,
                    0,
                    0,
                    SWP_NOACTIVATE | SWP_NOCOPYBITS | SWP_NOOWNERZORDER | SWP_NOSIZE,
                )
            }
            .map_err(|error| windows_stage_error("WINDOW_FOLLOW", error))?;
            self.frame = next;
        }
        Ok(self.frame)
    }

    fn close_on_owner_thread(&self) {
        if unsafe { IsWindow(Some(self.hwnd)) }.as_bool() {
            let _ = unsafe { DestroyWindow(self.hwnd) };
        }
        self.published_hwnd.store(0, AtomicOrdering::Release);
    }
}

impl Drop for NativeCompositionSurface {
    fn drop(&mut self) {
        if !unsafe { IsWindow(Some(self.hwnd)) }.as_bool() {
            self.published_hwnd.store(0, AtomicOrdering::Release);
            return;
        }
        if unsafe { GetCurrentThreadId() } == self.creator_thread_id {
            self.close_on_owner_thread();
        } else {
            let _ = unsafe {
                PostMessageW(
                    Some(self.hwnd),
                    WM_CLOSE,
                    WPARAM::default(),
                    LPARAM::default(),
                )
            };
        }
    }
}

fn register_native_surface_class() -> Result<(), String> {
    NATIVE_SURFACE_CLASS_REGISTERED
        .get_or_init(|| {
            let module = unsafe { GetModuleHandleW(PCWSTR::null()) }
                .map_err(|error| windows_stage_error("WINDOW_CLASS_MODULE", error))?;
            let class = WNDCLASSW {
                lpfnWndProc: Some(native_surface_window_proc),
                hInstance: HINSTANCE(module.0),
                lpszClassName: NATIVE_SURFACE_CLASS,
                ..WNDCLASSW::default()
            };
            let atom = unsafe { RegisterClassW(&class) };
            if atom == 0 {
                return Err(windows_stage_error(
                    "WINDOW_CLASS_REGISTER",
                    windows::core::Error::from_thread(),
                ));
            }
            Ok(())
        })
        .clone()
}

unsafe extern "system" fn native_surface_window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    match message {
        WM_NCHITTEST => LRESULT(HTTRANSPARENT as isize),
        // The Tauri tracking window owns monitor and DPI transitions. Destroying this HWND from
        // inside WM_DPICHANGED races the grouped move and can deadlock capture shutdown. The
        // render loop observes the changed monitor/frame and the host performs an orderly reset.
        WM_DISPLAYCHANGE | WM_DPICHANGED => LRESULT(0),
        WM_POWERBROADCAST if wparam.0 as u32 == PBT_APMSUSPEND => {
            let _ = unsafe { DestroyWindow(hwnd) };
            LRESULT(1)
        }
        WM_CLOSE => {
            let _ = unsafe { DestroyWindow(hwnd) };
            LRESULT(0)
        }
        _ => unsafe { DefWindowProcW(hwnd, message, wparam, lparam) },
    }
}

struct InputHostBackdropComposition {
    _target: DesktopWindowTarget,
    root: ContainerVisual,
    backdrop: SpriteVisual,
    _backdrop_brush: CompositionBackdropBrush,
    geometry: CompositionRoundedRectangleGeometry,
    _clip: CompositionGeometricClip,
}

impl InputHostBackdropComposition {
    fn new(
        compositor: &Compositor,
        desktop_interop: &ICompositorDesktopInterop,
        input_hwnd: HWND,
        surface_scale: f32,
        presentation: NativeGpuPresentation,
    ) -> Result<Self, String> {
        let host_backdrop_enabled = 1_i32;
        unsafe {
            DwmSetWindowAttribute(
                input_hwnd,
                DWMWA_USE_HOSTBACKDROPBRUSH,
                (&host_backdrop_enabled as *const i32).cast(),
                std::mem::size_of::<i32>() as u32,
            )
        }
        .map_err(|error| windows_stage_error("INPUT_HOST_BACKDROP_WINDOW_ATTRIBUTE", error))?;

        // The input target is below the WebView child. The DOM paints only text, icons, and the
        // foreground rim, so neither glyphs nor controls enter the backdrop sample.
        let target = unsafe { desktop_interop.CreateDesktopWindowTarget(input_hwnd, false) }
            .map_err(|error| windows_stage_error("INPUT_HOST_BACKDROP_TARGET", error))?;
        let root = compositor
            .CreateContainerVisual()
            .map_err(|error| windows_stage_error("INPUT_HOST_BACKDROP_ROOT", error))?;
        root.SetSize(Vector2 {
            X: 616.0 * surface_scale,
            Y: 360.0 * surface_scale,
        })
        .map_err(|error| windows_stage_error("INPUT_HOST_BACKDROP_ROOT_SIZE", error))?;
        target
            .SetRoot(&root)
            .map_err(|error| windows_stage_error("INPUT_HOST_BACKDROP_SET_ROOT", error))?;

        let backdrop = compositor
            .CreateSpriteVisual()
            .map_err(|error| windows_stage_error("INPUT_HOST_BACKDROP_VISUAL", error))?;
        let backdrop_brush = compositor
            .CreateHostBackdropBrush()
            .map_err(|error| windows_stage_error("INPUT_HOST_BACKDROP_BRUSH", error))?;
        backdrop
            .SetBrush(&backdrop_brush)
            .map_err(|error| windows_stage_error("INPUT_HOST_BACKDROP_SET_BRUSH", error))?;
        let geometry = compositor
            .CreateRoundedRectangleGeometry()
            .map_err(|error| windows_stage_error("INPUT_HOST_BACKDROP_GEOMETRY", error))?;
        let clip = compositor
            .CreateGeometricClipWithGeometry(&geometry)
            .map_err(|error| windows_stage_error("INPUT_HOST_BACKDROP_CLIP", error))?;
        backdrop
            .SetClip(&clip)
            .map_err(|error| windows_stage_error("INPUT_HOST_BACKDROP_SET_CLIP", error))?;
        root.Children()
            .and_then(|children| children.InsertAtBottom(&backdrop))
            .map_err(|error| windows_stage_error("INPUT_HOST_BACKDROP_INSERT", error))?;

        let mut input = Self {
            _target: target,
            root,
            backdrop,
            _backdrop_brush: backdrop_brush,
            geometry,
            _clip: clip,
        };
        input.update(surface_scale, presentation)?;
        Ok(input)
    }

    fn update(
        &mut self,
        surface_scale: f32,
        presentation: NativeGpuPresentation,
    ) -> Result<(), String> {
        let width =
            (presentation.capsule_half_width * 2.0 + 16.0).clamp(220.0, 360.0) * surface_scale;
        let height = presentation.input_surface_height.clamp(64.0, 104.0) * surface_scale;
        let left = match presentation.expansion_direction {
            NativeGpuExpansionDirection::Right => 8.0 * surface_scale,
            NativeGpuExpansionDirection::Left => {
                616.0 * surface_scale - width - 8.0 * surface_scale
            }
        };
        let top = 352.0 * surface_scale - height;
        let size = Vector2 {
            X: width.max(1.0),
            Y: height.max(1.0),
        };
        self.root
            .SetSize(Vector2 {
                X: 616.0 * surface_scale,
                Y: 360.0 * surface_scale,
            })
            .and_then(|()| self.backdrop.SetSize(size))
            .and_then(|()| {
                self.backdrop.SetOffset(Vector3 {
                    X: left,
                    Y: top,
                    Z: 0.0,
                })
            })
            .and_then(|()| self.geometry.SetSize(size))
            .and_then(|()| {
                self.geometry.SetCornerRadius(Vector2 {
                    X: height * 0.5,
                    Y: height * 0.5,
                })
            })
            .and_then(|()| {
                self.backdrop
                    .SetOpacity(if presentation.input_surface_visible {
                        1.0
                    } else {
                        0.0
                    })
            })
            .map_err(|error| windows_stage_error("INPUT_HOST_BACKDROP_UPDATE", error))
    }
}

struct HostBackdropComposition {
    _dispatcher_queue: DispatcherQueueController,
    _compositor: Compositor,
    _target: DesktopWindowTarget,
    _root: ContainerVisual,
    backdrop: SpriteVisual,
    _backdrop_brush: CompositionBackdropBrush,
    backdrop_geometry: CompositionEllipseGeometry,
    backdrop_clip: CompositionGeometricClip,
    _foreground_brush: CompositionSurfaceBrush,
    _foreground: SpriteVisual,
    input: Option<InputHostBackdropComposition>,
}

impl HostBackdropComposition {
    fn new(
        hwnd: HWND,
        input_hwnd: HWND,
        swap_chain: &IDXGISwapChain1,
        frame: PhysicalFrame,
        presentation: NativeGpuPresentation,
    ) -> Result<Self, String> {
        let host_backdrop_enabled = 1_i32;
        unsafe {
            DwmSetWindowAttribute(
                hwnd,
                DWMWA_USE_HOSTBACKDROPBRUSH,
                (&host_backdrop_enabled as *const i32).cast(),
                std::mem::size_of::<i32>() as u32,
            )
        }
        .map_err(|error| windows_stage_error("HOST_BACKDROP_WINDOW_ATTRIBUTE", error))?;

        let dispatcher_queue = unsafe {
            CreateDispatcherQueueController(DispatcherQueueOptions {
                dwSize: std::mem::size_of::<DispatcherQueueOptions>() as u32,
                threadType: DQTYPE_THREAD_CURRENT,
                apartmentType: DQTAT_COM_NONE,
            })
        }
        .map_err(|error| windows_stage_error("HOST_BACKDROP_DISPATCHER_QUEUE", error))?;
        let compositor = Compositor::new()
            .map_err(|error| windows_stage_error("HOST_BACKDROP_COMPOSITOR", error))?;
        let desktop_interop: ICompositorDesktopInterop = compositor
            .cast()
            .map_err(|error| windows_stage_error("HOST_BACKDROP_DESKTOP_INTEROP", error))?;
        let target = unsafe { desktop_interop.CreateDesktopWindowTarget(hwnd, true) }
            .map_err(|error| windows_stage_error("HOST_BACKDROP_TARGET", error))?;
        let root = compositor
            .CreateContainerVisual()
            .map_err(|error| windows_stage_error("HOST_BACKDROP_ROOT", error))?;
        let output_size = Vector2 {
            X: frame.width as f32,
            Y: frame.height as f32,
        };
        root.SetSize(output_size)
            .map_err(|error| windows_stage_error("HOST_BACKDROP_ROOT_SIZE", error))?;
        target
            .SetRoot(&root)
            .map_err(|error| windows_stage_error("HOST_BACKDROP_SET_ROOT", error))?;
        let root_children = root
            .Children()
            .map_err(|error| windows_stage_error("HOST_BACKDROP_ROOT_CHILDREN", error))?;

        // Keep the center on the compositor's live HostBackdrop. Windows deliberately rejects
        // transforms on backdrop brushes, so geometric edge optics are drawn from an independent
        // monitor texture while this layer remains an identity sample with no recursive capture.
        let backdrop = compositor
            .CreateSpriteVisual()
            .map_err(|error| windows_stage_error("HOST_BACKDROP_SOURCE_VISUAL", error))?;
        backdrop
            .SetSize(output_size)
            .map_err(|error| windows_stage_error("HOST_BACKDROP_SOURCE_SIZE", error))?;
        let backdrop_brush = compositor
            .CreateHostBackdropBrush()
            .map_err(|error| windows_stage_error("HOST_BACKDROP_BRUSH", error))?;
        backdrop
            .SetBrush(&backdrop_brush)
            .map_err(|error| windows_stage_error("HOST_BACKDROP_SOURCE_BRUSH", error))?;
        let backdrop_geometry = compositor
            .CreateEllipseGeometry()
            .map_err(|error| windows_stage_error("HOST_BACKDROP_GEOMETRY", error))?;
        let backdrop_clip = compositor
            .CreateGeometricClipWithGeometry(&backdrop_geometry)
            .map_err(|error| windows_stage_error("HOST_BACKDROP_CLIP", error))?;
        backdrop
            .SetClip(&backdrop_clip)
            .map_err(|error| windows_stage_error("HOST_BACKDROP_SET_CLIP", error))?;
        root_children
            .InsertAtBottom(&backdrop)
            .map_err(|error| windows_stage_error("HOST_BACKDROP_INSERT_SOURCE", error))?;

        let compositor_interop: ICompositorInterop = compositor
            .cast()
            .map_err(|error| windows_stage_error("HOST_BACKDROP_SWAPCHAIN_INTEROP", error))?;
        let composition_surface =
            unsafe { compositor_interop.CreateCompositionSurfaceForSwapChain(swap_chain) }
                .map_err(|error| windows_stage_error("HOST_BACKDROP_SWAPCHAIN_SURFACE", error))?;
        let foreground_brush = compositor
            .CreateSurfaceBrushWithSurface(&composition_surface)
            .map_err(|error| windows_stage_error("HOST_BACKDROP_FOREGROUND_BRUSH", error))?;
        foreground_brush
            .SetStretch(CompositionStretch::Fill)
            .map_err(|error| windows_stage_error("HOST_BACKDROP_FOREGROUND_STRETCH", error))?;
        let foreground = compositor
            .CreateSpriteVisual()
            .map_err(|error| windows_stage_error("HOST_BACKDROP_FOREGROUND", error))?;
        foreground
            .SetSize(output_size)
            .map_err(|error| windows_stage_error("HOST_BACKDROP_FOREGROUND_SIZE", error))?;
        foreground
            .SetBrush(&foreground_brush)
            .map_err(|error| windows_stage_error("HOST_BACKDROP_SET_FOREGROUND", error))?;
        root_children
            .InsertAtTop(&foreground)
            .map_err(|error| windows_stage_error("HOST_BACKDROP_INSERT_FOREGROUND", error))?;

        let input = match InputHostBackdropComposition::new(
            &compositor,
            &desktop_interop,
            input_hwnd,
            frame.width as f32 / 640.0,
            presentation,
        ) {
            Ok(input) => Some(input),
            Err(error) => {
                eprintln!("[presence-native-gpu] native input glass unavailable: {error}");
                None
            }
        };
        let mut composition = Self {
            _dispatcher_queue: dispatcher_queue,
            _compositor: compositor,
            _target: target,
            _root: root,
            backdrop,
            _backdrop_brush: backdrop_brush,
            backdrop_geometry,
            backdrop_clip,
            _foreground_brush: foreground_brush,
            _foreground: foreground,
            input,
        };
        composition.update_lens(presentation, NativeDragSample::default(), frame)?;
        Ok(composition)
    }

    fn update_lens(
        &mut self,
        presentation: NativeGpuPresentation,
        drag: NativeDragSample,
        frame: PhysicalFrame,
    ) -> Result<(), String> {
        let surface_scale = frame.width as f32 / 640.0;
        let center = Vector2 {
            X: presentation.core_x * surface_scale,
            Y: presentation.core_y * surface_scale,
        };
        let direction_length =
            (drag.direction[0] * drag.direction[0] + drag.direction[1] * drag.direction[1]).sqrt();
        let direction = if direction_length > 0.01 {
            [
                drag.direction[0] / direction_length,
                drag.direction[1] / direction_length,
            ]
        } else {
            [1.0, 0.0]
        };
        let deformation = drag.stretch * 0.055 + drag.release * 0.026;
        let rotation = direction[1].atan2(direction[0]);

        let radius = LIQUID_CORE_RADIUS * surface_scale;
        self.backdrop_geometry
            .SetCenter(center)
            .and_then(|()| {
                self.backdrop_geometry.SetRadius(Vector2 {
                    X: radius * (1.0 + deformation),
                    Y: radius * (1.0 - deformation * 0.45),
                })
            })
            .map_err(|error| windows_stage_error("HOST_BACKDROP_UPDATE_GEOMETRY", error))?;
        self.backdrop_clip
            .SetCenterPoint(center)
            .and_then(|()| self.backdrop_clip.SetRotationAngle(rotation))
            .map_err(|error| windows_stage_error("HOST_BACKDROP_UPDATE_CLIP", error))?;
        self.backdrop
            .SetSize(Vector2 {
                X: frame.width as f32,
                Y: frame.height as f32,
            })
            .map_err(|error| windows_stage_error("HOST_BACKDROP_UPDATE_SIZE", error))?;
        if let Some(input) = self.input.as_mut() {
            input.update(surface_scale, presentation)?;
        }
        Ok(())
    }
}

struct DesktopEdgeCapture {
    duplication: IDXGIOutputDuplication,
    texture: Option<ID3D11Texture2D>,
    view: Option<ID3D11ShaderResourceView>,
    texture_size: (u32, u32),
    generation: u64,
    resume_after_generation: u64,
    last_frame_at: Option<Instant>,
}

impl DesktopEdgeCapture {
    fn new(device: &ID3D11Device, monitor: HMONITOR) -> Result<Self, String> {
        let dxgi_device: IDXGIDevice = device
            .cast()
            .map_err(|error| windows_stage_error("EDGE_CAPTURE_DXGI_DEVICE", error))?;
        let adapter = unsafe { dxgi_device.GetAdapter() }
            .map_err(|error| windows_stage_error("EDGE_CAPTURE_ADAPTER", error))?;
        let mut output_index = 0;
        let output = loop {
            let candidate = match unsafe { adapter.EnumOutputs(output_index) } {
                Ok(output) => output,
                Err(error) if error.code() == DXGI_ERROR_NOT_FOUND => {
                    return Err("PRESENCE_NATIVE_GPU_EDGE_CAPTURE_OUTPUT_NOT_FOUND".to_owned());
                }
                Err(error) => {
                    return Err(windows_stage_error("EDGE_CAPTURE_ENUM_OUTPUT", error));
                }
            };
            output_index += 1;
            let description = unsafe { candidate.GetDesc() }
                .map_err(|error| windows_stage_error("EDGE_CAPTURE_OUTPUT_DESC", error))?;
            if description.Monitor == monitor {
                break candidate;
            }
        };
        let output: IDXGIOutput1 = output
            .cast()
            .map_err(|error| windows_stage_error("EDGE_CAPTURE_OUTPUT1", error))?;
        let duplication = unsafe { output.DuplicateOutput(&dxgi_device) }
            .map_err(|error| windows_stage_error("EDGE_CAPTURE_DUPLICATE_OUTPUT", error))?;
        Ok(Self {
            duplication,
            texture: None,
            view: None,
            texture_size: (0, 0),
            generation: 0,
            resume_after_generation: 1,
            last_frame_at: None,
        })
    }

    fn acquire_latest(
        &mut self,
        device: &ID3D11Device,
        context: &ID3D11DeviceContext,
    ) -> Result<bool, String> {
        let mut frame_info = DXGI_OUTDUPL_FRAME_INFO::default();
        let mut resource: Option<IDXGIResource> = None;
        match unsafe {
            self.duplication
                .AcquireNextFrame(0, &mut frame_info, &mut resource)
        } {
            Err(error) if error.code() == DXGI_ERROR_WAIT_TIMEOUT => return Ok(false),
            Err(error) => return Err(windows_stage_error("EDGE_CAPTURE_ACQUIRE", error)),
            Ok(()) => {}
        }

        let copy_result = (|| {
            if frame_info.LastPresentTime == 0 {
                return Ok(false);
            }
            let source: ID3D11Texture2D = resource
                .ok_or_else(|| "PRESENCE_NATIVE_GPU_EDGE_CAPTURE_RESOURCE_MISSING".to_owned())?
                .cast()
                .map_err(|error| windows_stage_error("EDGE_CAPTURE_TEXTURE", error))?;
            let mut description = D3D11_TEXTURE2D_DESC::default();
            unsafe { source.GetDesc(&mut description) };
            self.ensure_texture(device, description)?;
            let target = self
                .texture
                .as_ref()
                .ok_or_else(|| "PRESENCE_NATIVE_GPU_EDGE_CAPTURE_TARGET_MISSING".to_owned())?;
            unsafe { context.CopyResource(target, &source) };
            self.generation = self.generation.saturating_add(1);
            self.last_frame_at = Some(Instant::now());
            Ok(true)
        })();
        let release_result = unsafe { self.duplication.ReleaseFrame() }
            .map_err(|error| windows_stage_error("EDGE_CAPTURE_RELEASE", error));
        match (copy_result, release_result) {
            (Err(error), _) => Err(error),
            (_, Err(error)) => Err(error),
            (Ok(copied), Ok(())) => Ok(copied),
        }
    }

    fn ensure_texture(
        &mut self,
        device: &ID3D11Device,
        source: D3D11_TEXTURE2D_DESC,
    ) -> Result<(), String> {
        if self.texture_size == (source.Width, source.Height)
            && self.texture.is_some()
            && self.view.is_some()
        {
            return Ok(());
        }
        let description = D3D11_TEXTURE2D_DESC {
            Width: source.Width,
            Height: source.Height,
            MipLevels: 1,
            ArraySize: 1,
            Format: source.Format,
            SampleDesc: source.SampleDesc,
            Usage: D3D11_USAGE_DEFAULT,
            BindFlags: D3D11_BIND_SHADER_RESOURCE.0 as u32,
            CPUAccessFlags: 0,
            MiscFlags: 0,
        };
        let mut texture = None;
        unsafe { device.CreateTexture2D(&description, None, Some(&mut texture)) }
            .map_err(|error| windows_stage_error("EDGE_CAPTURE_TARGET_CREATE", error))?;
        let texture = texture
            .ok_or_else(|| "PRESENCE_NATIVE_GPU_EDGE_CAPTURE_TARGET_UNAVAILABLE".to_owned())?;
        let mut view = None;
        unsafe { device.CreateShaderResourceView(&texture, None, Some(&mut view)) }
            .map_err(|error| windows_stage_error("EDGE_CAPTURE_VIEW_CREATE", error))?;
        self.texture = Some(texture);
        self.view = Some(
            view.ok_or_else(|| "PRESENCE_NATIVE_GPU_EDGE_CAPTURE_VIEW_UNAVAILABLE".to_owned())?,
        );
        self.texture_size = (source.Width, source.Height);
        Ok(())
    }

    fn rebase_after_drag(&mut self) {
        self.resume_after_generation = self.generation.saturating_add(EDGE_CAPTURE_REBASE_FRAMES);
    }

    fn is_ready(&self, drag_active: bool) -> bool {
        !drag_active && self.view.is_some() && self.generation >= self.resume_after_generation
    }
}

struct NativeCompositionRenderer {
    surface: NativeCompositionSurface,
    device: ID3D11Device,
    context: ID3D11DeviceContext,
    swap_chain: IDXGISwapChain3,
    composition: HostBackdropComposition,
    render_targets: Vec<Option<SwapChainRenderTarget>>,
    vertex_shader: ID3D11VertexShader,
    pixel_shader: ID3D11PixelShader,
    constants: ID3D11Buffer,
    sampler: ID3D11SamplerState,
    edge_capture: Option<DesktopEdgeCapture>,
    presentation: Arc<RwLock<NativeGpuPresentation>>,
    target_frame_rate: u16,
    display_refresh_rate_hz: u16,
    monitor_frame: PhysicalFrame,
    shape_motion: NativeShapeMotion,
    drag_motion: NativeDragMotion,
    state_motion: NativeStateMotion,
    hdr_capture: bool,
    hdr_checked_at: Instant,
    status: Arc<Mutex<NativeGpuStatus>>,
    reported_edge_ready: bool,
}

struct NativeStateMotion {
    state: NativeGpuVisualState,
    started_at: Instant,
    transition_started_at: Instant,
    transition_duration: Duration,
    transition_from: NativeVisualStyle,
    last_style: NativeVisualStyle,
}

impl NativeStateMotion {
    fn new(state: NativeGpuVisualState, started_at: Instant) -> Self {
        let style = native_visual_style(state, 0.0, 0.0, 0.0, false);
        Self {
            state,
            started_at,
            transition_started_at: started_at,
            transition_duration: Duration::ZERO,
            transition_from: style,
            last_style: style,
        }
    }

    fn sample(
        &mut self,
        state: NativeGpuVisualState,
        sampled_at: Instant,
        elapsed_seconds: f32,
        voice_level: f32,
        reduced_motion: bool,
    ) -> NativeVisualSample {
        if self.state != state {
            let previous_state = self.state;
            self.state = state;
            self.started_at = sampled_at;
            self.transition_started_at = sampled_at;
            self.transition_duration =
                native_visual_transition_duration(previous_state, state, reduced_motion);
            self.transition_from = self.last_style;
        }
        let state_elapsed_seconds = sampled_at
            .saturating_duration_since(self.started_at)
            .as_secs_f32()
            .min(3_600.0);
        let raw_progress = if self.transition_duration.is_zero() {
            1.0
        } else {
            sampled_at
                .saturating_duration_since(self.transition_started_at)
                .as_secs_f32()
                / self.transition_duration.as_secs_f32()
        };
        let transition_progress = ease_in_out_cubic(raw_progress.clamp(0.0, 1.0));
        let target = native_visual_style(
            state,
            elapsed_seconds,
            state_elapsed_seconds,
            voice_level,
            reduced_motion,
        );
        let style = self.transition_from.lerp(target, transition_progress);
        self.last_style = style;
        NativeVisualSample {
            style,
            state_elapsed_seconds,
            transition_progress,
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct NativeVisualStyle {
    energy: f32,
    accent: [f32; 3],
    pulse: f32,
    notify_wave: f32,
    voice_mix: f32,
}

impl NativeVisualStyle {
    fn lerp(self, target: Self, progress: f32) -> Self {
        Self {
            energy: lerp(self.energy, target.energy, progress),
            accent: [
                lerp(self.accent[0], target.accent[0], progress),
                lerp(self.accent[1], target.accent[1], progress),
                lerp(self.accent[2], target.accent[2], progress),
            ],
            pulse: lerp(self.pulse, target.pulse, progress),
            notify_wave: lerp(self.notify_wave, target.notify_wave, progress),
            voice_mix: lerp(self.voice_mix, target.voice_mix, progress),
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct NativeVisualSample {
    style: NativeVisualStyle,
    state_elapsed_seconds: f32,
    transition_progress: f32,
}

fn native_visual_transition_duration(
    previous: NativeGpuVisualState,
    next: NativeGpuVisualState,
    reduced_motion: bool,
) -> Duration {
    if reduced_motion {
        return Duration::from_millis(80);
    }
    if matches!(
        next,
        NativeGpuVisualState::Approval | NativeGpuVisualState::Error
    ) {
        return Duration::from_millis(160);
    }
    if matches!(previous, NativeGpuVisualState::Repositioning)
        || matches!(next, NativeGpuVisualState::Repositioning)
    {
        return Duration::from_millis(180);
    }
    if matches!(
        next,
        NativeGpuVisualState::Responding | NativeGpuVisualState::Speaking
    ) {
        return Duration::from_millis(240);
    }
    if matches!(
        next,
        NativeGpuVisualState::Returning | NativeGpuVisualState::Sleeping
    ) {
        return Duration::from_millis(320);
    }
    Duration::from_millis(280)
}

fn native_visual_style(
    state: NativeGpuVisualState,
    elapsed_seconds: f32,
    state_elapsed_seconds: f32,
    voice_level: f32,
    reduced_motion: bool,
) -> NativeVisualStyle {
    let energy = match state {
        NativeGpuVisualState::Suspended | NativeGpuVisualState::Sleeping => 0.035,
        NativeGpuVisualState::Idle | NativeGpuVisualState::Returning => 0.08,
        NativeGpuVisualState::Forming
        | NativeGpuVisualState::Input
        | NativeGpuVisualState::Options
        | NativeGpuVisualState::Aware => 0.14,
        NativeGpuVisualState::Repositioning => 0.16,
        NativeGpuVisualState::Submitting
        | NativeGpuVisualState::Thinking
        | NativeGpuVisualState::Tool
        | NativeGpuVisualState::Notify
        | NativeGpuVisualState::Approval
        | NativeGpuVisualState::Error => 0.18,
        NativeGpuVisualState::Responding | NativeGpuVisualState::Speaking => 0.22,
    };
    let accent = match state {
        NativeGpuVisualState::Tool | NativeGpuVisualState::Approval => [0.96, 0.67, 0.24],
        NativeGpuVisualState::Responding
        | NativeGpuVisualState::Speaking
        | NativeGpuVisualState::Notify => [0.34, 0.86, 0.72],
        NativeGpuVisualState::Error => [0.96, 0.40, 0.35],
        NativeGpuVisualState::Suspended | NativeGpuVisualState::Sleeping => [0.52, 0.60, 0.66],
        _ => [0.34, 0.78, 1.0],
    };
    let pulse = if reduced_motion {
        1.0
    } else {
        match state {
            NativeGpuVisualState::Thinking | NativeGpuVisualState::Tool => {
                0.84 + 0.16 * (state_elapsed_seconds * 1.963_495_4).sin()
            }
            NativeGpuVisualState::Responding => 0.86 + 0.14 * (state_elapsed_seconds * 2.8).sin(),
            NativeGpuVisualState::Submitting => 0.88 + 0.12 * (-state_elapsed_seconds * 5.0).exp(),
            _ => 0.82 + 0.18 * (elapsed_seconds * 2.4).sin(),
        }
    };
    let notify_wave = if state == NativeGpuVisualState::Notify && !reduced_motion {
        let progress = (state_elapsed_seconds / 0.6).clamp(0.0, 1.0);
        (progress * std::f32::consts::PI).sin() * (1.0 - progress)
    } else {
        0.0
    };
    NativeVisualStyle {
        energy,
        accent,
        pulse,
        notify_wave,
        voice_mix: if state == NativeGpuVisualState::Speaking {
            voice_level.clamp(0.0, 1.0)
        } else {
            0.0
        },
    }
}

fn lerp(start: f32, end: f32, progress: f32) -> f32 {
    start + (end - start) * progress.clamp(0.0, 1.0)
}

#[derive(Clone)]
struct SwapChainRenderTarget {
    view: ID3D11RenderTargetView,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
struct NativeShape {
    droplet: f32,
    bridge: f32,
    capsule: f32,
}

impl NativeShape {
    fn target(presentation: NativeGpuPresentation) -> Self {
        let mut target = Self {
            droplet: presentation.shape_droplet,
            bridge: presentation.shape_bridge,
            capsule: presentation.shape_capsule,
        };
        if presentation.capsule_visible
            && !presentation.returning
            && target.droplet <= f32::EPSILON
            && target.bridge <= f32::EPSILON
            && target.capsule <= f32::EPSILON
        {
            target.capsule = 1.0;
        }
        target
    }

    fn scale(self, factor: f32) -> Self {
        Self {
            droplet: self.droplet * factor,
            bridge: self.bridge * factor,
            capsule: self.capsule * factor,
        }
    }
}

#[derive(Default)]
struct ShapeSpring {
    value: f32,
    velocity: f32,
}

impl ShapeSpring {
    fn set_immediate(&mut self, value: f32) {
        self.value = value.clamp(0.0, 1.0);
        self.velocity = 0.0;
    }

    fn step(&mut self, target: f32, delta_seconds: f32) {
        const MAXIMUM_STEP_SECONDS: f32 = 1.0 / 120.0;
        const MAXIMUM_OVERSHOOT: f32 = 0.04;
        const FREQUENCY_HZ: f32 = 5.5;
        const DAMPING_RATIO: f32 = 0.82;
        let steps = (delta_seconds / MAXIMUM_STEP_SECONDS).ceil().max(1.0) as u32;
        let step = delta_seconds / steps as f32;
        let angular_frequency = std::f32::consts::TAU * FREQUENCY_HZ;
        for _ in 0..steps {
            let acceleration = angular_frequency * angular_frequency * (target - self.value)
                - 2.0 * DAMPING_RATIO * angular_frequency * self.velocity;
            self.velocity += acceleration * step;
            self.value += self.velocity * step;
            let bounded = self
                .value
                .clamp(-MAXIMUM_OVERSHOOT, 1.0 + MAXIMUM_OVERSHOOT);
            if bounded != self.value {
                self.velocity = 0.0;
            }
            self.value = bounded;
        }
        if (target - self.value).abs() < 0.0001 && self.velocity.abs() < 0.001 {
            self.set_immediate(target);
        }
    }

    fn sample(&self) -> f32 {
        self.value.clamp(0.0, 1.0)
    }
}

struct NativeReturnEnvelope {
    started_at: Instant,
    origin: NativeShape,
}

#[derive(Default)]
struct NativeShapeMotion {
    droplet: ShapeSpring,
    bridge: ShapeSpring,
    capsule: ShapeSpring,
    last_sampled_at: Option<Instant>,
    returning: Option<NativeReturnEnvelope>,
    was_returning: bool,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
struct NativeDragSample {
    direction: [f32; 2],
    stretch: f32,
    release: f32,
}

struct NativeDragMotion {
    last_frame: Option<PhysicalFrame>,
    last_sampled_at: Option<Instant>,
    filtered_velocity: [f32; 2],
    direction: [f32; 2],
    stretch: f32,
    release_started_at: Option<Instant>,
    release_amplitude: f32,
    was_dragging: bool,
}

impl Default for NativeDragMotion {
    fn default() -> Self {
        Self {
            last_frame: None,
            last_sampled_at: None,
            filtered_velocity: [0.0; 2],
            direction: [1.0, 0.0],
            stretch: 0.0,
            release_started_at: None,
            release_amplitude: 0.0,
            was_dragging: false,
        }
    }
}

impl NativeDragMotion {
    fn sample(
        &mut self,
        frame: PhysicalFrame,
        sampled_at: Instant,
        dragging: bool,
        reduced_motion: bool,
    ) -> NativeDragSample {
        let delta_seconds = self
            .last_sampled_at
            .replace(sampled_at)
            .map_or(0.0, |previous| {
                sampled_at
                    .saturating_duration_since(previous)
                    .as_secs_f32()
                    .clamp(0.001, 0.05)
            });
        let frame_delta = self.last_frame.replace(frame).map_or([0.0; 2], |previous| {
            [
                frame.x.saturating_sub(previous.x) as f32,
                frame.y.saturating_sub(previous.y) as f32,
            ]
        });
        if reduced_motion {
            self.filtered_velocity = [0.0; 2];
            self.stretch = 0.0;
            self.release_started_at = None;
            self.release_amplitude = 0.0;
            self.was_dragging = dragging;
            return NativeDragSample {
                direction: self.direction,
                ..NativeDragSample::default()
            };
        }

        if dragging {
            self.release_started_at = None;
            let inverse_delta = if delta_seconds > 0.0 {
                1.0 / delta_seconds
            } else {
                0.0
            };
            let instantaneous = [
                frame_delta[0] * inverse_delta,
                frame_delta[1] * inverse_delta,
            ];
            let velocity_blend = 1.0 - (-delta_seconds * 18.0).exp();
            self.filtered_velocity[0] =
                lerp(self.filtered_velocity[0], instantaneous[0], velocity_blend);
            self.filtered_velocity[1] =
                lerp(self.filtered_velocity[1], instantaneous[1], velocity_blend);
            let speed = self.filtered_velocity[0].hypot(self.filtered_velocity[1]);
            if speed > 8.0 {
                self.direction = [
                    self.filtered_velocity[0] / speed,
                    self.filtered_velocity[1] / speed,
                ];
            }
            let target = (speed / 1_600.0).clamp(0.0, 1.0);
            let stretch_blend = 1.0 - (-delta_seconds * 20.0).exp();
            self.stretch = lerp(self.stretch, target, stretch_blend).clamp(0.0, 1.0);
        } else {
            if self.was_dragging {
                self.release_started_at = Some(sampled_at);
                self.release_amplitude = self.stretch.clamp(0.0, 1.0);
            }
            let decay = (-delta_seconds * 14.0).exp();
            self.filtered_velocity[0] *= decay;
            self.filtered_velocity[1] *= decay;
            self.stretch *= decay;
        }
        self.was_dragging = dragging;

        let release = self.release_started_at.map_or(0.0, |started_at| {
            let elapsed = sampled_at
                .saturating_duration_since(started_at)
                .as_secs_f32();
            if elapsed >= 0.34 {
                self.release_started_at = None;
                self.release_amplitude = 0.0;
                0.0
            } else {
                self.release_amplitude
                    * (-elapsed * 12.0).exp()
                    * (elapsed * std::f32::consts::TAU * 5.25).sin()
            }
        });
        NativeDragSample {
            direction: self.direction,
            stretch: self.stretch.clamp(0.0, 1.0),
            release: release.clamp(-1.0, 1.0),
        }
    }
}

impl NativeShapeMotion {
    fn sample(&mut self, presentation: NativeGpuPresentation, sampled_at: Instant) -> NativeShape {
        let delta_seconds = self
            .last_sampled_at
            .replace(sampled_at)
            .map_or(0.0, |previous| {
                sampled_at
                    .saturating_duration_since(previous)
                    .as_secs_f32()
                    .clamp(0.0, 0.05)
            });
        if presentation.returning && !self.was_returning {
            self.returning = Some(NativeReturnEnvelope {
                started_at: sampled_at,
                origin: self.current(),
            });
        } else if !presentation.returning {
            self.returning = None;
        }
        self.was_returning = presentation.returning;

        if presentation.reduced_motion {
            let target = if presentation.returning {
                NativeShape::default()
            } else {
                NativeShape::target(presentation)
            };
            self.set_immediate(target);
            return target;
        }
        if let Some(envelope) = &self.returning {
            let elapsed = sampled_at
                .saturating_duration_since(envelope.started_at)
                .as_secs_f32();
            let progress = (elapsed / 0.22).clamp(0.0, 1.0);
            let eased = ease_in_out_cubic(progress);
            let mut shape = envelope.origin.scale(1.0 - eased);
            if progress < 1.0 && envelope.origin.bridge < 0.01 {
                let bridge = (std::f32::consts::PI * progress).sin() * envelope.origin.capsule;
                shape.bridge = shape.bridge.max(bridge);
                shape.droplet = shape.droplet.max(bridge * 0.72);
            }
            self.set_immediate(shape);
            return shape;
        }

        let target = NativeShape::target(presentation);
        self.droplet.step(target.droplet, delta_seconds);
        self.bridge.step(target.bridge, delta_seconds);
        self.capsule.step(target.capsule, delta_seconds);
        self.current()
    }

    fn current(&self) -> NativeShape {
        NativeShape {
            droplet: self.droplet.sample(),
            bridge: self.bridge.sample(),
            capsule: self.capsule.sample(),
        }
    }

    fn set_immediate(&mut self, shape: NativeShape) {
        self.droplet.set_immediate(shape.droplet);
        self.bridge.set_immediate(shape.bridge);
        self.capsule.set_immediate(shape.capsule);
    }
}

fn ease_in_out_cubic(value: f32) -> f32 {
    if value < 0.5 {
        4.0 * value * value * value
    } else {
        1.0 - (-2.0 * value + 2.0).powi(3) / 2.0
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
struct PresenceConstants {
    output_size: [f32; 2],
    capture_size: [f32; 2],
    source_origin_px: [f32; 2],
    core_center_px: [f32; 2],
    capsule_center_px: [f32; 2],
    elapsed_seconds: f32,
    surface_scale: f32,
    shape_droplet: f32,
    shape_bridge: f32,
    shape_capsule: f32,
    expansion_direction: f32,
    visual_state: f32,
    material_opacity: f32,
    voice_level: f32,
    reduced_motion: f32,
    reduced_transparency: f32,
    increased_contrast: f32,
    particles_enabled: f32,
    diagnostic_solid: f32,
    capture_linear: f32,
    capsule_half_width: f32,
    state_elapsed_seconds: f32,
    drag_active: f32,
    capture_source_valid: f32,
    state_energy: f32,
    state_pulse: f32,
    state_notify_wave: f32,
    state_voice_mix: f32,
    state_accent: [f32; 3],
    state_transition_progress: f32,
    drag_direction: [f32; 2],
    drag_stretch: f32,
    drag_release: f32,
    constants_padding: [f32; 3],
}

struct NativeCompositionRendererInput {
    config: NativeGpuConfig,
    monitor_frame: PhysicalFrame,
    display_refresh_rate_hz: u16,
    surface_hwnd: Arc<AtomicIsize>,
    presentation: Arc<RwLock<NativeGpuPresentation>>,
    hdr_capture: bool,
    status: Arc<Mutex<NativeGpuStatus>>,
}

impl NativeCompositionRenderer {
    fn new(
        device: ID3D11Device,
        context: ID3D11DeviceContext,
        input: NativeCompositionRendererInput,
    ) -> Result<Self, String> {
        let NativeCompositionRendererInput {
            config,
            monitor_frame,
            display_refresh_rate_hz,
            surface_hwnd,
            presentation,
            hdr_capture,
            status,
        } = input;
        let surface = NativeCompositionSurface::new(config, surface_hwnd)?;
        let swap_chain = create_swap_chain(&device, config.render_frame)?;
        let render_targets = vec![None; SWAP_CHAIN_BUFFER_COUNT as usize];
        let (vertex_shader, pixel_shader) = create_shaders(&device)?;
        let constants = create_constant_buffer(&device)?;
        let sampler = create_linear_sampler(&device)?;
        let monitor = monitor_handle_for_presentation(config.render_frame, config.presentation)
            .map_err(|error| error.to_string())?;
        let edge_capture = match DesktopEdgeCapture::new(&device, monitor) {
            Ok(capture) => {
                update_status(&status, |status| {
                    status.capture_source_stage = "desktop_edge_capture_ready".to_owned();
                    status.capture_source_hresult = None;
                    status.fallback_reason = None;
                });
                Some(capture)
            }
            Err(error) => {
                update_status(&status, |status| {
                    status.capture_source_stage = "host_backdrop_only".to_owned();
                    status.capture_source_hresult = None;
                    status.fallback_reason =
                        Some("PRESENCE_NATIVE_GPU_EDGE_CAPTURE_UNAVAILABLE".to_owned());
                });
                let _ = error;
                None
            }
        };
        let swap_chain_base: IDXGISwapChain1 = swap_chain
            .cast()
            .map_err(|error| windows_stage_error("HOST_BACKDROP_SWAPCHAIN_CAST", error))?;
        let composition = HostBackdropComposition::new(
            surface.hwnd,
            surface.input_hwnd,
            &swap_chain_base,
            config.render_frame,
            config.presentation,
        )?;
        Ok(Self {
            surface,
            device,
            context,
            swap_chain,
            composition,
            render_targets,
            vertex_shader,
            pixel_shader,
            constants,
            sampler,
            edge_capture,
            presentation,
            target_frame_rate: config.target_frame_rate,
            display_refresh_rate_hz,
            monitor_frame,
            shape_motion: NativeShapeMotion::default(),
            drag_motion: NativeDragMotion::default(),
            state_motion: NativeStateMotion::new(config.presentation.visual_state, Instant::now()),
            hdr_capture,
            hdr_checked_at: Instant::now(),
            status,
            reported_edge_ready: false,
        })
    }

    fn present(&mut self, elapsed: Duration, drag_active: bool) -> Result<u64, String> {
        if !unsafe { IsWindow(Some(self.surface.hwnd)) }.as_bool() {
            return Err("PRESENCE_NATIVE_GPU_SURFACE_INVALIDATED".to_owned());
        }
        let presentation = *self
            .presentation
            .read()
            .map_err(|_| "PRESENCE_NATIVE_GPU_PRESENTATION_LOCK_FAILED".to_owned())?;
        let render_frame = self.surface.sync_to_tracking_window()?;
        let active_monitor = monitor_handle_for_presentation(render_frame, presentation)
            .map_err(|error| error.to_string())?;
        let active_monitor_frame =
            monitor_frame(active_monitor).map_err(|error| error.to_string())?;
        if active_monitor_frame != self.monitor_frame {
            return Err("PRESENCE_NATIVE_GPU_MONITOR_CHANGED".to_owned());
        }
        if self.hdr_checked_at.elapsed() >= Duration::from_secs(1) {
            self.hdr_checked_at = Instant::now();
            if let Ok(active_hdr) = monitor_uses_hdr(active_monitor) {
                if active_hdr != self.hdr_capture {
                    return Err("PRESENCE_NATIVE_GPU_COLOR_SPACE_CHANGED".to_owned());
                }
            }
        }
        let sampled_at = Instant::now();
        let shape = self.shape_motion.sample(presentation, sampled_at);
        let drag = self.drag_motion.sample(
            render_frame,
            sampled_at,
            drag_active,
            presentation.reduced_motion,
        );
        let state_sample = self.state_motion.sample(
            presentation.visual_state,
            sampled_at,
            elapsed.as_secs_f32(),
            presentation.voice_level,
            presentation.reduced_motion,
        );
        self.composition
            .update_lens(presentation, drag, render_frame)?;
        let capture_error = self
            .edge_capture
            .as_mut()
            .and_then(|capture| capture.acquire_latest(&self.device, &self.context).err());
        if let Some(error) = capture_error {
            update_status(&self.status, |status| {
                status.capture_source_stage = "desktop_edge_capture_failed".to_owned();
                status.capture_source_hresult = None;
                status.optics_source = NativeGpuOpticsSource::HostBackdrop;
                status.fallback_reason =
                    Some("PRESENCE_NATIVE_GPU_EDGE_CAPTURE_UNAVAILABLE".to_owned());
            });
            let _ = error;
            self.edge_capture = None;
        }
        let edge_capture_ready = self
            .edge_capture
            .as_ref()
            .is_some_and(|capture| capture.is_ready(drag_active));
        let capture_generation = self
            .edge_capture
            .as_ref()
            .map_or(0, |capture| capture.generation);
        let capture_view = edge_capture_ready
            .then(|| self.edge_capture.as_ref()?.view.clone())
            .flatten();
        if edge_capture_ready != self.reported_edge_ready {
            self.reported_edge_ready = edge_capture_ready;
            update_status(&self.status, |status| {
                status.optics_source = if edge_capture_ready {
                    NativeGpuOpticsSource::HostBackdropPlusMonitorEdge
                } else {
                    NativeGpuOpticsSource::HostBackdrop
                };
                status.capture_source_stage = if edge_capture_ready {
                    "desktop_edge_capture_active"
                } else {
                    "host_backdrop_only"
                }
                .to_owned();
            });
        }
        let constants = PresenceConstants {
            output_size: [render_frame.width as f32, render_frame.height as f32],
            capture_size: [
                self.monitor_frame.width as f32,
                self.monitor_frame.height as f32,
            ],
            source_origin_px: [
                render_frame.x.saturating_sub(self.monitor_frame.x) as f32,
                render_frame.y.saturating_sub(self.monitor_frame.y) as f32,
            ],
            core_center_px: [
                presentation.core_x * (render_frame.width as f32 / 640.0),
                presentation.core_y * (render_frame.width as f32 / 640.0),
            ],
            capsule_center_px: [
                presentation.capsule_x * (render_frame.width as f32 / 640.0),
                presentation.capsule_y * (render_frame.width as f32 / 640.0),
            ],
            elapsed_seconds: elapsed.as_secs_f32(),
            surface_scale: render_frame.width as f32 / 640.0,
            shape_droplet: shape.droplet,
            shape_bridge: shape.bridge,
            shape_capsule: shape.capsule,
            expansion_direction: expansion_direction_value(presentation.expansion_direction),
            visual_state: visual_state_value(presentation.visual_state),
            material_opacity: presentation.opacity,
            voice_level: presentation.voice_level,
            reduced_motion: f32::from(presentation.reduced_motion),
            reduced_transparency: f32::from(presentation.reduced_transparency),
            increased_contrast: f32::from(presentation.increased_contrast),
            particles_enabled: f32::from(presentation.particles_enabled),
            diagnostic_solid: diagnostic_solid_output(),
            capture_linear: 0.0,
            capsule_half_width: presentation.capsule_half_width,
            state_elapsed_seconds: state_sample.state_elapsed_seconds,
            drag_active: f32::from(drag_active),
            // HostBackdrop remains the identity center. The monitor texture is restricted to the
            // outer optical band and is disabled while dragging or waiting for fresh post-drag
            // frames, preventing the previous Fairy position from feeding back into the lens.
            capture_source_valid: f32::from(edge_capture_ready),
            state_energy: state_sample.style.energy,
            state_pulse: state_sample.style.pulse,
            state_notify_wave: state_sample.style.notify_wave,
            state_voice_mix: state_sample.style.voice_mix,
            state_accent: state_sample.style.accent,
            state_transition_progress: state_sample.transition_progress,
            drag_direction: drag.direction,
            drag_stretch: drag.stretch,
            drag_release: drag.release,
            constants_padding: [0.0; 3],
        };
        let render_target = self.current_render_target()?;
        unsafe {
            self.context.UpdateSubresource(
                &self.constants,
                0,
                None,
                (&constants as *const PresenceConstants).cast(),
                0,
                0,
            );
            self.context
                .ClearRenderTargetView(&render_target.view, &[0.0; 4]);
            self.context
                .OMSetRenderTargets(Some(&[Some(render_target.view.clone())]), None);
            self.context.RSSetViewports(Some(&[D3D11_VIEWPORT {
                TopLeftX: 0.0,
                TopLeftY: 0.0,
                Width: render_frame.width as f32,
                Height: render_frame.height as f32,
                MinDepth: 0.0,
                MaxDepth: 1.0,
            }]));
            self.context
                .IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
            self.context.VSSetShader(&self.vertex_shader, None);
            self.context.PSSetShader(&self.pixel_shader, None);
            self.context
                .VSSetConstantBuffers(0, Some(&[Some(self.constants.clone())]));
            self.context
                .PSSetConstantBuffers(0, Some(&[Some(self.constants.clone())]));
            self.context
                .PSSetShaderResources(0, Some(&[capture_view.clone()]));
            self.context
                .PSSetSamplers(0, Some(&[Some(self.sampler.clone())]));
            self.context.Draw(3, 0);
            self.context.PSSetShaderResources(0, Some(&[None]));
            self.context.PSSetSamplers(0, Some(&[None]));
            self.context.OMSetRenderTargets(None, None);
        }
        let result = unsafe { self.swap_chain.Present(0, DXGI_PRESENT(0)) };
        result.ok().map_err(windows_error)?;
        self.surface.show()?;
        Ok(capture_generation)
    }

    fn rebase_after_drag(&mut self) -> Result<(), String> {
        self.surface.sync_to_tracking_window()?;
        if let Some(capture) = self.edge_capture.as_mut() {
            capture.rebase_after_drag();
        }
        Ok(())
    }

    fn presentation_frame_rate(&self) -> Result<u16, String> {
        let presentation = self
            .presentation
            .read()
            .map_err(|_| "PRESENCE_NATIVE_GPU_PRESENTATION_LOCK_FAILED".to_owned())?;
        Ok(effective_render_frame_rate(
            self.target_frame_rate.min(presentation.frame_rate_limit),
            self.display_refresh_rate_hz,
        ))
    }

    fn current_render_target(&mut self) -> Result<SwapChainRenderTarget, String> {
        let index = usize::try_from(unsafe { self.swap_chain.GetCurrentBackBufferIndex() })
            .map_err(|_| "PRESENCE_NATIVE_GPU_BACK_BUFFER_INDEX_INVALID".to_owned())?;
        let slot = self
            .render_targets
            .get(index)
            .ok_or_else(|| "PRESENCE_NATIVE_GPU_BACK_BUFFER_INDEX_INVALID".to_owned())?;
        if let Some(target) = slot {
            return Ok(target.clone());
        }

        let texture = current_swap_chain_texture(&self.swap_chain)?;
        let target = create_render_target(&self.device, texture, index as u32)?;
        self.render_targets[index] = Some(target.clone());
        Ok(target)
    }
}

fn diagnostic_solid_output() -> f32 {
    if cfg!(debug_assertions) && std::env::var_os("FAIRY_PRESENCE_GPU_DIAGNOSTIC_SOLID").is_some() {
        1.0
    } else {
        0.0
    }
}

fn create_native_d3d_device(
    status: &Arc<Mutex<NativeGpuStatus>>,
) -> Result<(ID3D11Device, ID3D11DeviceContext), NativeGpuError> {
    update_status(status, |status| {
        status.capture_source_stage = "creating_d3d11_device".to_owned();
    });
    let feature_levels = [
        D3D_FEATURE_LEVEL_11_1,
        D3D_FEATURE_LEVEL_11_0,
        D3D_FEATURE_LEVEL_10_1,
        D3D_FEATURE_LEVEL_10_0,
    ];
    let mut device = None;
    let mut context = None;
    let mut feature_level = D3D_FEATURE_LEVEL::default();
    unsafe {
        D3D11CreateDevice(
            None,
            D3D_DRIVER_TYPE_HARDWARE,
            HMODULE::default(),
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            Some(&feature_levels),
            D3D11_SDK_VERSION,
            Some(&mut device),
            Some(&mut feature_level),
            Some(&mut context),
        )
    }
    .map_err(|error| composition_source_windows_error(status, "D3D11_DEVICE", error))?;
    if feature_level.0 < D3D_FEATURE_LEVEL_10_0.0 {
        return Err(composition_source_failure(
            status,
            "d3d11_feature_level",
            "PRESENCE_NATIVE_GPU_D3D11_FEATURE_LEVEL_UNSUPPORTED".to_owned(),
        ));
    }
    let device = device.ok_or_else(|| {
        composition_source_failure(
            status,
            "d3d11_device",
            "PRESENCE_NATIVE_GPU_D3D11_DEVICE_UNAVAILABLE".to_owned(),
        )
    })?;
    let context = context.ok_or_else(|| {
        composition_source_failure(
            status,
            "d3d11_context",
            "PRESENCE_NATIVE_GPU_D3D11_CONTEXT_UNAVAILABLE".to_owned(),
        )
    })?;
    update_status(status, |status| {
        status.capture_source_stage = "d3d11_device_created".to_owned();
        status.capture_source_hresult = None;
    });
    Ok((device, context))
}

fn create_swap_chain(
    device: &ID3D11Device,
    frame: PhysicalFrame,
) -> Result<IDXGISwapChain3, String> {
    let dxgi_device: IDXGIDevice = device
        .cast()
        .map_err(|error| windows_stage_error("SWAP_CHAIN_DXGI_DEVICE", error))?;
    let adapter = unsafe { dxgi_device.GetAdapter() }
        .map_err(|error| windows_stage_error("SWAP_CHAIN_ADAPTER", error))?;
    let factory: IDXGIFactory2 = unsafe { adapter.GetParent() }
        .map_err(|error| windows_stage_error("SWAP_CHAIN_FACTORY", error))?;
    let description = DXGI_SWAP_CHAIN_DESC1 {
        Width: frame.width,
        Height: frame.height,
        Format: DXGI_FORMAT_B8G8R8A8_UNORM,
        Stereo: false.into(),
        SampleDesc: DXGI_SAMPLE_DESC {
            Count: 1,
            Quality: 0,
        },
        BufferUsage: DXGI_USAGE_RENDER_TARGET_OUTPUT,
        BufferCount: SWAP_CHAIN_BUFFER_COUNT,
        Scaling: DXGI_SCALING_STRETCH,
        SwapEffect: DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL,
        AlphaMode: DXGI_ALPHA_MODE_PREMULTIPLIED,
        Flags: 0,
    };
    let swap_chain = unsafe { factory.CreateSwapChainForComposition(device, &description, None) }
        .map_err(|error| windows_stage_error("SWAP_CHAIN_CREATE", error))?;
    swap_chain
        .cast()
        .map_err(|error| windows_stage_error("SWAP_CHAIN_V3_CAST", error))
}

fn current_swap_chain_texture(swap_chain: &IDXGISwapChain3) -> Result<ID3D11Texture2D, String> {
    let base: IDXGISwapChain1 = swap_chain
        .cast()
        .map_err(|error| windows_stage_error("RENDER_TARGET_SWAP_CHAIN_CAST", error))?;
    unsafe { base.GetBuffer(0) }
        .map_err(|error| windows_stage_error("RENDER_TARGET_GET_CURRENT_BUFFER", error))
}

fn create_render_target(
    device: &ID3D11Device,
    texture: ID3D11Texture2D,
    index: u32,
) -> Result<SwapChainRenderTarget, String> {
    let mut description = D3D11_TEXTURE2D_DESC::default();
    unsafe { texture.GetDesc(&mut description) };
    let mut target = None;
    unsafe {
        device
            .CreateRenderTargetView(&texture, None, Some(&mut target))
            .map_err(|error| {
                format!(
                    "PRESENCE_NATIVE_GPU_RENDER_TARGET_CREATE_FAILED: index={index}, size={}x{}, format={}, bind_flags={}, usage={}, samples={}; {error}",
                    description.Width,
                    description.Height,
                    description.Format.0,
                    description.BindFlags,
                    description.Usage.0,
                    description.SampleDesc.Count,
                )
            })?;
    }
    Ok(SwapChainRenderTarget {
        view: target.ok_or_else(|| "PRESENCE_NATIVE_GPU_RENDER_TARGET_UNAVAILABLE".to_owned())?,
    })
}

fn create_shaders(
    device: &ID3D11Device,
) -> Result<(ID3D11VertexShader, ID3D11PixelShader), String> {
    let vertex_blob = compile_shader(b"vs_main\0", b"vs_5_0\0")?;
    let pixel_blob = compile_shader(b"ps_main\0", b"ps_5_0\0")?;
    let vertex_bytes = blob_bytes(&vertex_blob);
    let pixel_bytes = blob_bytes(&pixel_blob);
    let mut vertex_shader = None;
    let mut pixel_shader = None;
    unsafe {
        device
            .CreateVertexShader(vertex_bytes, None, Some(&mut vertex_shader))
            .map_err(windows_error)?;
        device
            .CreatePixelShader(pixel_bytes, None, Some(&mut pixel_shader))
            .map_err(windows_error)?;
    }
    Ok((
        vertex_shader.ok_or_else(|| "PRESENCE_NATIVE_GPU_VERTEX_SHADER_UNAVAILABLE".to_owned())?,
        pixel_shader.ok_or_else(|| "PRESENCE_NATIVE_GPU_PIXEL_SHADER_UNAVAILABLE".to_owned())?,
    ))
}

fn compile_shader(entry: &'static [u8], target: &'static [u8]) -> Result<ID3DBlob, String> {
    compile_shader_source(SHADER_SOURCE, entry, target, "LIQUID_GLASS")
}

fn compile_shader_source(
    source: &str,
    entry: &'static [u8],
    target: &'static [u8],
    stage: &str,
) -> Result<ID3DBlob, String> {
    let mut bytecode = None;
    let mut errors = None;
    let result = unsafe {
        D3DCompile(
            source.as_ptr().cast(),
            source.len(),
            PCSTR::null(),
            None,
            None::<&ID3DInclude>,
            PCSTR::from_raw(entry.as_ptr()),
            PCSTR::from_raw(target.as_ptr()),
            0,
            0,
            &mut bytecode,
            Some(&mut errors),
        )
    };
    if let Err(error) = result {
        let detail = errors
            .as_ref()
            .map(|blob| String::from_utf8_lossy(blob_bytes(blob)).trim().to_owned())
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| error.to_string());
        return Err(format!(
            "PRESENCE_NATIVE_GPU_SHADER_COMPILE_FAILED: stage={stage}; {detail}"
        ));
    }
    bytecode.ok_or_else(|| "PRESENCE_NATIVE_GPU_SHADER_BYTECODE_UNAVAILABLE".to_owned())
}

fn blob_bytes(blob: &ID3DBlob) -> &[u8] {
    unsafe {
        std::slice::from_raw_parts(blob.GetBufferPointer().cast::<u8>(), blob.GetBufferSize())
    }
}

fn create_constant_buffer(device: &ID3D11Device) -> Result<ID3D11Buffer, String> {
    create_constant_buffer_with_size(
        device,
        std::mem::size_of::<PresenceConstants>(),
        "CONSTANT_BUFFER_CREATE",
    )
}

fn create_constant_buffer_with_size(
    device: &ID3D11Device,
    byte_width: usize,
    stage: &str,
) -> Result<ID3D11Buffer, String> {
    if byte_width == 0 || !byte_width.is_multiple_of(16) {
        return Err(format!(
            "PRESENCE_NATIVE_GPU_CONSTANT_BUFFER_SIZE_INVALID: stage={stage}; bytes={byte_width}"
        ));
    }
    let description = D3D11_BUFFER_DESC {
        ByteWidth: u32::try_from(byte_width)
            .map_err(|_| "PRESENCE_NATIVE_GPU_CONSTANT_BUFFER_SIZE_INVALID".to_owned())?,
        Usage: D3D11_USAGE_DEFAULT,
        BindFlags: D3D11_BIND_CONSTANT_BUFFER.0 as u32,
        CPUAccessFlags: 0,
        MiscFlags: 0,
        StructureByteStride: 0,
    };
    let mut buffer = None;
    unsafe {
        device
            .CreateBuffer(&description, None, Some(&mut buffer))
            .map_err(|error| windows_stage_error(stage, error))?;
    }
    buffer.ok_or_else(|| "PRESENCE_NATIVE_GPU_CONSTANT_BUFFER_UNAVAILABLE".to_owned())
}

fn create_linear_sampler(device: &ID3D11Device) -> Result<ID3D11SamplerState, String> {
    let description = D3D11_SAMPLER_DESC {
        Filter: D3D11_FILTER_MIN_MAG_MIP_LINEAR,
        AddressU: D3D11_TEXTURE_ADDRESS_CLAMP,
        AddressV: D3D11_TEXTURE_ADDRESS_CLAMP,
        AddressW: D3D11_TEXTURE_ADDRESS_CLAMP,
        MipLODBias: 0.0,
        MaxAnisotropy: 1,
        ComparisonFunc: D3D11_COMPARISON_NEVER,
        BorderColor: [0.0; 4],
        MinLOD: 0.0,
        MaxLOD: f32::MAX,
    };
    let mut sampler = None;
    unsafe { device.CreateSamplerState(&description, Some(&mut sampler)) }
        .map_err(|error| windows_stage_error("EDGE_CAPTURE_SAMPLER", error))?;
    sampler.ok_or_else(|| "PRESENCE_NATIVE_GPU_EDGE_CAPTURE_SAMPLER_UNAVAILABLE".to_owned())
}

struct NativeDesktopCompositionSource {
    monitor_frame: PhysicalFrame,
    display_refresh_rate_hz: u16,
    hdr_capture: bool,
}

struct DisplayOutputDiagnostics {
    hdr_capture: bool,
    adapter_name: String,
    adapter_index: u32,
    output_device_name: String,
    output_index: u32,
    color_space: String,
}

struct CompositionWinRtMta {
    cookie: CO_MTA_USAGE_COOKIE,
}

impl CompositionWinRtMta {
    fn acquire(status: &Arc<Mutex<NativeGpuStatus>>) -> Result<Self, NativeGpuError> {
        let cookie = unsafe { CoIncrementMTAUsage() }
            .map_err(|error| composition_source_windows_error(status, "MTA_USAGE", error))?;
        if let Err(error) = unsafe { RoInitialize(RO_INIT_MULTITHREADED) } {
            unsafe {
                let _ = CoDecrementMTAUsage(cookie);
            }
            return Err(composition_source_windows_error(
                status,
                "WINRT_INIT",
                error,
            ));
        }
        Ok(Self { cookie })
    }
}

impl Drop for CompositionWinRtMta {
    fn drop(&mut self) {
        unsafe {
            RoUninitialize();
            let _ = CoDecrementMTAUsage(self.cookie);
        }
    }
}

fn inspect_desktop_composition_source(
    frame: PhysicalFrame,
    presentation: NativeGpuPresentation,
    status: &Arc<Mutex<NativeGpuStatus>>,
) -> Result<NativeDesktopCompositionSource, NativeGpuError> {
    let handle = monitor_handle_for_presentation(frame, presentation)?;
    update_status(status, |status| {
        status.capture_source_stage = "monitor_selected".to_owned();
        status.monitor_handle = Some(format_monitor_handle(handle));
    });
    let monitor = monitor_diagnostics(handle)?;
    let monitor_frame = monitor.frame;
    let display_refresh_rate_hz = monitor.display_refresh_rate_hz;
    let output = display_output_diagnostics(handle).ok();
    let hdr_capture = output
        .as_ref()
        .is_some_and(|diagnostics| diagnostics.hdr_capture);
    update_status(status, |status| {
        status.capture_source_stage = "monitor_validated".to_owned();
        status.monitor_x = monitor_frame.x;
        status.monitor_y = monitor_frame.y;
        status.monitor_width = monitor_frame.width;
        status.monitor_height = monitor_frame.height;
        status.display_refresh_rate_hz = display_refresh_rate_hz;
        status.monitor_device_name = Some(monitor.device_name.clone());
        status.monitor_friendly_name = None;
        status.adapter_name = output.as_ref().map(|value| value.adapter_name.clone());
        status.adapter_index = output.as_ref().map(|value| value.adapter_index);
        status.output_device_name = output
            .as_ref()
            .map(|value| value.output_device_name.clone());
        status.output_index = output.as_ref().map(|value| value.output_index);
        status.hdr_color_space = output.as_ref().map(|value| value.color_space.clone());
        status.hdr_capture = hdr_capture;
        status.capture_item_width = 0;
        status.capture_item_height = 0;
        status.capture_window_handle = None;
        status.capture_source_stage = "host_backdrop_monitor_validated".to_owned();
        status.capture_source_hresult = None;
    });
    Ok(NativeDesktopCompositionSource {
        monitor_frame,
        display_refresh_rate_hz,
        hdr_capture,
    })
}

struct MonitorDiagnostics {
    frame: PhysicalFrame,
    device_name: String,
    display_refresh_rate_hz: u16,
}

fn monitor_diagnostics(handle: HMONITOR) -> Result<MonitorDiagnostics, NativeGpuError> {
    let mut info = MONITORINFOEXW::default();
    info.monitorInfo.cbSize = std::mem::size_of::<MONITORINFOEXW>() as u32;
    if !unsafe { GetMonitorInfoW(handle, &mut info.monitorInfo) }.as_bool() {
        return Err(NativeGpuError::StartFailed(
            "PRESENCE_NATIVE_GPU_MONITOR_INFO_UNAVAILABLE".to_owned(),
        ));
    }
    let width = u32::try_from(
        info.monitorInfo
            .rcMonitor
            .right
            .saturating_sub(info.monitorInfo.rcMonitor.left),
    )
    .map_err(|_| NativeGpuError::InvalidSurface)?;
    let height = u32::try_from(
        info.monitorInfo
            .rcMonitor
            .bottom
            .saturating_sub(info.monitorInfo.rcMonitor.top),
    )
    .map_err(|_| NativeGpuError::InvalidSurface)?;
    let mut display_mode = DEVMODEW {
        dmSize: std::mem::size_of::<DEVMODEW>() as u16,
        ..DEVMODEW::default()
    };
    let has_display_mode = unsafe {
        EnumDisplaySettingsW(
            PCWSTR(info.szDevice.as_ptr()),
            ENUM_CURRENT_SETTINGS,
            &mut display_mode,
        )
    }
    .as_bool();
    Ok(MonitorDiagnostics {
        frame: PhysicalFrame {
            x: info.monitorInfo.rcMonitor.left,
            y: info.monitorInfo.rcMonitor.top,
            width,
            height,
        },
        device_name: utf16z(&info.szDevice),
        display_refresh_rate_hz: has_display_mode
            .then_some(display_mode.dmDisplayFrequency)
            .and_then(valid_display_refresh_rate)
            .unwrap_or(0),
    })
}

fn set_surface_always_on_top(hwnd: HWND, always_on_top: bool) -> Result<(), String> {
    unsafe {
        SetWindowPos(
            hwnd,
            Some(if always_on_top {
                HWND_TOPMOST
            } else {
                HWND_NOTOPMOST
            }),
            0,
            0,
            0,
            0,
            SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOOWNERZORDER | SWP_NOSIZE,
        )
    }
    .map_err(|error| windows_stage_error("WINDOW_LEVEL", error))
}

fn monitor_handle_for_presentation(
    frame: PhysicalFrame,
    presentation: NativeGpuPresentation,
) -> Result<HMONITOR, NativeGpuError> {
    let handle = unsafe {
        MonitorFromPoint(
            capture_probe_point(frame, presentation),
            MONITOR_DEFAULTTONEAREST,
        )
    };
    if handle.is_invalid() {
        return Err(NativeGpuError::StartFailed(
            "PRESENCE_NATIVE_GPU_MONITOR_UNAVAILABLE".to_owned(),
        ));
    }
    Ok(handle)
}

fn capture_probe_point(frame: PhysicalFrame, presentation: NativeGpuPresentation) -> POINT {
    let scale = frame.width as f32 / 640.0;
    POINT {
        x: frame
            .x
            .saturating_add((presentation.core_x * scale).round() as i32),
        y: frame
            .y
            .saturating_add((presentation.core_y * scale).round() as i32),
    }
}

fn monitor_uses_hdr(handle: HMONITOR) -> Result<bool, NativeGpuError> {
    display_output_diagnostics(handle).map(|diagnostics| diagnostics.hdr_capture)
}

fn display_output_diagnostics(
    handle: HMONITOR,
) -> Result<DisplayOutputDiagnostics, NativeGpuError> {
    let factory: IDXGIFactory1 = unsafe { CreateDXGIFactory1() }
        .map_err(|error| NativeGpuError::StartFailed(windows_stage_error("HDR_FACTORY", error)))?;
    for adapter_index in 0..64 {
        let adapter = match unsafe { factory.EnumAdapters1(adapter_index) } {
            Ok(adapter) => adapter,
            Err(error) if error.code() == DXGI_ERROR_NOT_FOUND => break,
            Err(error) => {
                return Err(NativeGpuError::StartFailed(windows_stage_error(
                    "HDR_ADAPTER_ENUM",
                    error,
                )))
            }
        };
        for output_index in 0..64 {
            let output = match unsafe { adapter.EnumOutputs(output_index) } {
                Ok(output) => output,
                Err(error) if error.code() == DXGI_ERROR_NOT_FOUND => break,
                Err(error) => {
                    return Err(NativeGpuError::StartFailed(windows_stage_error(
                        "HDR_OUTPUT_ENUM",
                        error,
                    )))
                }
            };
            let description = unsafe { output.GetDesc() }.map_err(|error| {
                NativeGpuError::StartFailed(windows_stage_error("HDR_OUTPUT_DESC", error))
            })?;
            if description.Monitor != handle {
                continue;
            }
            let adapter_description = unsafe { adapter.GetDesc1() }.map_err(|error| {
                NativeGpuError::StartFailed(windows_stage_error("HDR_ADAPTER_DESC", error))
            })?;
            let output: IDXGIOutput6 = output.cast().map_err(|error| {
                NativeGpuError::StartFailed(windows_stage_error("HDR_OUTPUT6", error))
            })?;
            let description = unsafe { output.GetDesc1() }.map_err(|error| {
                NativeGpuError::StartFailed(windows_stage_error("HDR_OUTPUT_DESC1", error))
            })?;
            return Ok(DisplayOutputDiagnostics {
                hdr_capture: color_space_uses_hdr(description.ColorSpace),
                adapter_name: utf16z(&adapter_description.Description),
                adapter_index,
                output_device_name: utf16z(&description.DeviceName),
                output_index,
                color_space: format!("{}", description.ColorSpace.0),
            });
        }
    }
    Err(NativeGpuError::StartFailed(
        "PRESENCE_NATIVE_GPU_HDR_OUTPUT_UNAVAILABLE".to_owned(),
    ))
}

fn utf16z(value: &[u16]) -> String {
    let length = value
        .iter()
        .position(|character| *character == 0)
        .unwrap_or(value.len());
    String::from_utf16_lossy(&value[..length])
}

fn color_space_uses_hdr(
    color_space: windows::Win32::Graphics::Dxgi::Common::DXGI_COLOR_SPACE_TYPE,
) -> bool {
    matches!(
        color_space,
        DXGI_COLOR_SPACE_RGB_FULL_G10_NONE_P709 | DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020
    )
}

fn monitor_frame(handle: HMONITOR) -> Result<PhysicalFrame, NativeGpuError> {
    let mut info = MONITORINFO {
        cbSize: std::mem::size_of::<MONITORINFO>() as u32,
        ..MONITORINFO::default()
    };
    if !unsafe { GetMonitorInfoW(handle, &mut info) }.as_bool() {
        return Err(NativeGpuError::StartFailed(
            "PRESENCE_NATIVE_GPU_MONITOR_INFO_UNAVAILABLE".to_owned(),
        ));
    }
    let width = u32::try_from(info.rcMonitor.right.saturating_sub(info.rcMonitor.left))
        .map_err(|_| NativeGpuError::InvalidSurface)?;
    let height = u32::try_from(info.rcMonitor.bottom.saturating_sub(info.rcMonitor.top))
        .map_err(|_| NativeGpuError::InvalidSurface)?;
    Ok(PhysicalFrame {
        x: info.rcMonitor.left,
        y: info.rcMonitor.top,
        width,
        height,
    })
}

fn update_status(status: &Arc<Mutex<NativeGpuStatus>>, update: impl FnOnce(&mut NativeGpuStatus)) {
    if let Ok(mut status) = status.lock() {
        update(&mut status);
    }
}

fn push_metric(metrics: &mut VecDeque<f64>, value: f64) {
    if !value.is_finite() || value < 0.0 {
        return;
    }
    if metrics.len() >= METRIC_WINDOW {
        metrics.pop_front();
    }
    metrics.push_back(value);
}

fn percentile(metrics: &VecDeque<f64>, percentile: f64) -> Option<f64> {
    if metrics.is_empty() {
        return None;
    }
    let mut values = metrics.iter().copied().collect::<Vec<_>>();
    values.sort_by(|left, right| left.partial_cmp(right).unwrap_or(Ordering::Equal));
    let index = ((values.len() - 1) as f64 * percentile.clamp(0.0, 1.0)).round() as usize;
    values.get(index).copied()
}

fn visual_state_value(state: NativeGpuVisualState) -> f32 {
    match state {
        NativeGpuVisualState::Idle => 0.0,
        NativeGpuVisualState::Aware => 1.0,
        NativeGpuVisualState::Forming => 2.0,
        NativeGpuVisualState::Input => 3.0,
        NativeGpuVisualState::Options => 4.0,
        NativeGpuVisualState::Submitting => 5.0,
        NativeGpuVisualState::Thinking => 6.0,
        NativeGpuVisualState::Tool => 7.0,
        NativeGpuVisualState::Responding => 8.0,
        NativeGpuVisualState::Speaking => 9.0,
        NativeGpuVisualState::Notify => 10.0,
        NativeGpuVisualState::Approval => 11.0,
        NativeGpuVisualState::Error => 12.0,
        NativeGpuVisualState::Returning => 13.0,
        NativeGpuVisualState::Suspended => 14.0,
        NativeGpuVisualState::Repositioning => 15.0,
        NativeGpuVisualState::Sleeping => 16.0,
    }
}

fn expansion_direction_value(direction: NativeGpuExpansionDirection) -> f32 {
    match direction {
        NativeGpuExpansionDirection::Left => -1.0,
        NativeGpuExpansionDirection::Right => 1.0,
    }
}

fn now_ms() -> Option<u64> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()
        .and_then(|duration| u64::try_from(duration.as_millis()).ok())
}

fn windows_error(error: windows::core::Error) -> String {
    format!("PRESENCE_NATIVE_GPU_WINDOWS_ERROR: {error}")
}

fn windows_stage_error(stage: &str, error: windows::core::Error) -> String {
    format!("PRESENCE_NATIVE_GPU_{stage}_FAILED: {error}")
}

fn composition_source_failure(
    status: &Arc<Mutex<NativeGpuStatus>>,
    stage: &str,
    message: String,
) -> NativeGpuError {
    update_status(status, |status| {
        status.capture_source_stage = format!("{stage}_failed");
        status.capture_source_hresult = None;
    });
    NativeGpuError::StartFailed(message)
}

fn composition_source_windows_error(
    status: &Arc<Mutex<NativeGpuStatus>>,
    stage: &str,
    error: windows::core::Error,
) -> NativeGpuError {
    let hresult = format!("0x{:08X}", error.code().0 as u32);
    update_status(status, |status| {
        status.capture_source_stage = format!("{}_failed", stage.to_ascii_lowercase());
        status.capture_source_hresult = Some(hresult.clone());
    });
    NativeGpuError::StartFailed(format!(
        "PRESENCE_NATIVE_GPU_{stage}_FAILED: HRESULT={hresult}: {}",
        error.message()
    ))
}

fn format_monitor_handle(handle: HMONITOR) -> String {
    format!("0x{:016X}", handle.0 as usize)
}

#[cfg(test)]
mod tests {
    use super::*;
    use windows::Win32::Graphics::Dxgi::Common::DXGI_COLOR_SPACE_RGB_FULL_G22_NONE_P709;

    #[test]
    fn percentile_uses_the_requested_tail_without_panicking_on_empty_samples() {
        assert_eq!(percentile(&VecDeque::new(), 0.95), None);
        let samples = VecDeque::from([1.0, 2.0, 3.0, 4.0, 100.0]);
        assert_eq!(percentile(&samples, 0.95), Some(100.0));
        assert_eq!(percentile(&samples, 0.0), Some(1.0));
    }

    #[test]
    fn liquid_glass_hlsl_compiles_for_the_production_shader_model() {
        compile_shader(b"vs_main\0", b"vs_5_0\0")
            .unwrap_or_else(|error| panic!("vertex shader failed: {error}"));
        compile_shader(b"ps_main\0", b"ps_5_0\0")
            .unwrap_or_else(|error| panic!("pixel shader failed: {error}"));
    }

    #[test]
    fn liquid_glass_shader_uses_continuous_visual_style_inputs() {
        let shader = include_str!("liquid_glass.hlsl");
        for required in [
            "float state_energy;",
            "float state_pulse;",
            "float state_notify_wave;",
            "float state_voice_mix;",
            "float3 state_accent;",
        ] {
            assert!(shader.contains(required), "missing style input: {required}");
        }
        assert!(!shader.contains("visual_state =="));
        assert!(!shader.contains("visual_state !="));
    }

    #[test]
    fn constant_buffer_remains_aligned_for_d3d11() {
        assert_eq!(std::mem::size_of::<PresenceConstants>() % 16, 0);
        assert_eq!(std::mem::size_of::<PresenceConstants>(), 176);
    }

    #[test]
    fn visual_state_transition_continues_from_the_last_displayed_style() {
        let started_at = Instant::now();
        let mut motion = NativeStateMotion::new(NativeGpuVisualState::Idle, started_at);
        let idle = motion.sample(NativeGpuVisualState::Idle, started_at, 0.0, 0.0, false);
        let tool_start = motion.sample(NativeGpuVisualState::Tool, started_at, 0.0, 0.0, false);
        assert_visual_style_close(idle.style, tool_start.style);

        let interrupted_at = started_at + Duration::from_millis(140);
        let tool_midpoint =
            motion.sample(NativeGpuVisualState::Tool, interrupted_at, 0.14, 0.0, false);
        let error_start = motion.sample(
            NativeGpuVisualState::Error,
            interrupted_at,
            0.14,
            0.0,
            false,
        );
        assert_visual_style_close(tool_midpoint.style, error_start.style);
        assert_eq!(error_start.transition_progress, 0.0);
    }

    #[test]
    fn reduced_motion_uses_a_short_material_dissolve() {
        let started_at = Instant::now();
        let mut motion = NativeStateMotion::new(NativeGpuVisualState::Idle, started_at);
        motion.sample(NativeGpuVisualState::Idle, started_at, 0.0, 0.0, true);
        let speaking = motion.sample(NativeGpuVisualState::Speaking, started_at, 0.0, 0.7, true);
        assert_eq!(speaking.transition_progress, 0.0);
        let completed = motion.sample(
            NativeGpuVisualState::Speaking,
            started_at + Duration::from_millis(80),
            0.08,
            0.7,
            true,
        );
        assert_eq!(completed.transition_progress, 1.0);
        assert!((completed.style.voice_mix - 0.7).abs() < 0.000_1);
    }

    #[test]
    fn semantic_states_keep_one_material_and_only_change_bounded_energy_cues() {
        let idle = native_visual_style(NativeGpuVisualState::Idle, 0.0, 0.0, 0.0, false);
        let sleeping = native_visual_style(NativeGpuVisualState::Sleeping, 0.0, 0.0, 0.0, false);
        let tool = native_visual_style(NativeGpuVisualState::Tool, 0.0, 0.0, 0.0, false);
        let speaking = native_visual_style(NativeGpuVisualState::Speaking, 0.0, 0.0, 0.8, false);

        assert!(sleeping.energy < idle.energy);
        assert!(tool.energy > idle.energy);
        assert_eq!(tool.accent, [0.96, 0.67, 0.24]);
        assert_eq!(speaking.accent, [0.34, 0.86, 0.72]);
        assert!((speaking.voice_mix - 0.8).abs() < 0.000_1);
        for style in [idle, sleeping, tool, speaking] {
            assert!((0.0..=0.25).contains(&style.energy));
        }
    }

    fn assert_visual_style_close(left: NativeVisualStyle, right: NativeVisualStyle) {
        assert!((left.energy - right.energy).abs() < 0.000_1);
        assert!((left.pulse - right.pulse).abs() < 0.000_1);
        assert!((left.notify_wave - right.notify_wave).abs() < 0.000_1);
        assert!((left.voice_mix - right.voice_mix).abs() < 0.000_1);
        for index in 0..3 {
            assert!((left.accent[index] - right.accent[index]).abs() < 0.000_1);
        }
    }

    #[test]
    fn capture_source_tracks_the_fairy_core_instead_of_the_wide_surface_center() {
        let frame = PhysicalFrame {
            x: 1_383,
            y: 790,
            width: 640,
            height: 260,
        };
        let point = capture_probe_point(
            frame,
            NativeGpuPresentation {
                core_x: 544.0,
                core_y: 88.0,
                expansion_direction: NativeGpuExpansionDirection::Left,
                ..NativeGpuPresentation::default()
            },
        );

        assert_eq!((point.x, point.y), (1_927, 878));
        assert_ne!(point.x, frame.x + frame.width as i32 / 2);
    }

    #[test]
    fn hdr_capture_is_selected_only_for_linear_or_pq_outputs() {
        assert!(color_space_uses_hdr(
            DXGI_COLOR_SPACE_RGB_FULL_G10_NONE_P709
        ));
        assert!(color_space_uses_hdr(
            DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020
        ));
        assert!(!color_space_uses_hdr(
            DXGI_COLOR_SPACE_RGB_FULL_G22_NONE_P709
        ));
    }

    #[test]
    fn stable_capsule_has_no_bridge_and_return_restores_only_a_transient_bridge() {
        let started = Instant::now();
        let mut motion = NativeShapeMotion::default();
        let stable = NativeGpuPresentation {
            capsule_visible: true,
            shape_capsule: 1.0,
            reduced_motion: true,
            ..NativeGpuPresentation::default()
        };
        assert_eq!(
            motion.sample(stable, started),
            NativeShape {
                droplet: 0.0,
                bridge: 0.0,
                capsule: 1.0,
            }
        );

        let returning = NativeGpuPresentation {
            returning: true,
            reduced_motion: false,
            ..stable
        };
        motion.sample(returning, started);
        let midpoint = motion.sample(returning, started + Duration::from_millis(110));
        assert!(midpoint.bridge > 0.9);
        assert!(midpoint.capsule > 0.4 && midpoint.capsule < 0.6);
        assert_eq!(
            motion.sample(returning, started + Duration::from_millis(220)),
            NativeShape::default()
        );
    }

    #[test]
    fn shape_springs_never_expose_more_than_four_percent_overshoot() {
        let mut spring = ShapeSpring::default();
        for _ in 0..240 {
            spring.step(1.0, 1.0 / 144.0);
            assert!((-0.04..=1.04).contains(&spring.value));
        }
        assert!((spring.sample() - 1.0).abs() < 0.001);
    }

    #[test]
    fn native_drag_motion_tracks_velocity_and_settles_with_bounded_gel_response() {
        let started = Instant::now();
        let frame = PhysicalFrame {
            x: 100,
            y: 100,
            width: 640,
            height: 260,
        };
        let mut motion = NativeDragMotion::default();
        assert_eq!(
            motion.sample(frame, started, false, false),
            NativeDragSample {
                direction: [1.0, 0.0],
                ..NativeDragSample::default()
            }
        );
        let moving = motion.sample(
            PhysicalFrame { x: 116, ..frame },
            started + Duration::from_millis(16),
            true,
            false,
        );
        assert!(moving.direction[0] > 0.99);
        assert!(moving.direction[1].abs() < 0.01);
        assert!(moving.stretch > 0.0 && moving.stretch <= 1.0);

        motion.sample(
            PhysicalFrame { x: 116, ..frame },
            started + Duration::from_millis(32),
            false,
            false,
        );
        let settling = motion.sample(
            PhysicalFrame { x: 116, ..frame },
            started + Duration::from_millis(80),
            false,
            false,
        );
        assert!(settling.release.abs() <= 1.0);
        let settled = motion.sample(
            PhysicalFrame { x: 116, ..frame },
            started + Duration::from_millis(390),
            false,
            false,
        );
        assert_eq!(settled.release, 0.0);
        assert!(settled.stretch < moving.stretch);
    }

    #[test]
    fn reduced_motion_disables_drag_deformation_and_elastic_release() {
        let started = Instant::now();
        let mut motion = NativeDragMotion::default();
        let frame = PhysicalFrame {
            x: 0,
            y: 0,
            width: 640,
            height: 260,
        };
        motion.sample(frame, started, false, false);
        let sample = motion.sample(
            PhysicalFrame { x: 20, ..frame },
            started + Duration::from_millis(16),
            true,
            true,
        );
        assert_eq!(sample.stretch, 0.0);
        assert_eq!(sample.release, 0.0);
    }

    #[test]
    fn render_deadline_supports_all_runtime_rates_without_catch_up_bursts() {
        assert_eq!(render_interval(15), Duration::from_secs_f64(1.0 / 15.0));
        assert_eq!(render_interval(30), Duration::from_secs_f64(1.0 / 30.0));
        assert_eq!(render_interval(60), Duration::from_secs_f64(1.0 / 60.0));
        assert_eq!(render_interval(144), Duration::from_secs_f64(1.0 / 144.0));
        assert_eq!(render_interval(300), Duration::from_secs_f64(1.0 / 300.0));

        let started = Instant::now();
        let interval = render_interval(144);
        let after_stall =
            advance_render_deadline(started, started + Duration::from_millis(100), interval);
        assert!(after_stall > started + Duration::from_millis(100));
        assert!(after_stall <= started + Duration::from_millis(100) + interval);
    }

    #[test]
    fn high_refresh_is_capped_to_the_active_display_and_uses_a_short_spin_tail() {
        assert_eq!(effective_render_frame_rate(300, 360), 300);
        assert_eq!(effective_render_frame_rate(300, 240), 240);
        assert_eq!(effective_render_frame_rate(300, 0), 300);
        assert_eq!(render_spin_window(300), Duration::from_micros(60));
        assert_eq!(render_spin_window(144), Duration::from_micros(100));
        assert_eq!(render_spin_window(60), Duration::from_micros(200));
    }

    #[test]
    fn hybrid_optics_keep_host_backdrop_identity_and_use_one_monitor_edge_texture() {
        let backend = include_str!("windows_backend.rs");
        let production = backend.split("#[cfg(test)]").next().unwrap_or(backend);
        for required in [
            "CreateHostBackdropBrush()",
            "inspect_desktop_composition_source",
            "D3D11CreateDevice(",
            "DuplicateOutput(&dxgi_device)",
            "CreateShaderResourceView(&texture",
            "DesktopEdgeCapture",
            "host_backdrop_renderer_started",
            "capture_source_hresult",
        ] {
            assert!(
                production.contains(required),
                "missing source contract: {required}"
            );
        }
        for forbidden in [
            "windows_capture",
            "GraphicsCaptureItem",
            "CreateForMonitor",
            "CreateForWindow",
            "NativeCaptureHandler",
            "fairy-wgc-source",
            "CompositionVisualSurface",
        ] {
            assert!(
                !production.contains(forbidden),
                "capture implementation returned: {forbidden}"
            );
        }
    }

    #[test]
    fn input_glass_uses_a_separate_host_backdrop_target_under_the_dom_overlay() {
        let backend = include_str!("windows_backend.rs");
        let production = backend.split("#[cfg(test)]").next().unwrap_or(backend);
        for required in [
            "struct InputHostBackdropComposition",
            "CreateDesktopWindowTarget(input_hwnd, false)",
            "CreateRoundedRectangleGeometry()",
            "presentation.input_surface_visible",
            "presentation.input_surface_height.clamp(64.0, 104.0)",
        ] {
            assert!(
                production.contains(required),
                "native input glass contract is missing {required}"
            );
        }
    }

    #[test]
    fn shader_uses_a_continuous_single_sample_edge_profile() {
        let shader = include_str!("liquid_glass.hlsl");
        for required in [
            "edge_refraction_profile",
            "smoothstep(0.10, 0.92, edge_focus)",
            "sample_refracted_desktop",
            "inward_depth * 1.65 + outside_clearance",
            "EDGE_MAX_DISPERSION_PX = 2.0",
        ] {
            assert!(
                shader.contains(required),
                "missing edge profile: {required}"
            );
        }
        assert!(!shader.contains("HOST_BACKDROP_LENS_BAND_COUNT"));
        assert!(!shader.contains("for ("));
    }

    #[test]
    fn dpi_reconfiguration_never_destroys_or_repositions_tauri_windows_from_render_thread() {
        let backend = include_str!("windows_backend.rs");
        let production = backend.split("#[cfg(test)]").next().unwrap_or(backend);
        assert!(production.contains("WM_DISPLAYCHANGE | WM_DPICHANGED => LRESULT(0)"));
        assert!(!production.contains("renderer.surface.show_tracking_fallback()"));
        assert!(!production.contains("fn show_tracking_fallback"));
    }

    #[test]
    fn native_surface_remains_visible_to_recording_and_remote_desktop() {
        let backend = include_str!("windows_backend.rs");
        let production = backend.split("#[cfg(test)]").next().unwrap_or(backend);
        assert!(production
            .contains("presence_backdrop::set_window_capture_excluded(hwnd.0 as isize, false)"));
        assert!(!production.contains("exclude_window_from_capture"));
    }

    #[test]
    fn flip_model_renders_and_clears_the_current_back_buffer() {
        let backend = include_str!("windows_backend.rs");
        let production = backend.split("#[cfg(test)]").next().unwrap_or(backend);
        assert!(production.contains("self.swap_chain.GetCurrentBackBufferIndex()"));
        assert!(production.contains("current_swap_chain_texture(&self.swap_chain)"));
        assert!(production.contains("self.render_targets[index] = Some(target.clone())"));
        assert!(production.contains("ClearRenderTargetView(&render_target.view, &[0.0; 4])"));
        assert!(!production.contains(".render_targets\n            .first()"));
    }

    #[test]
    fn edge_capture_never_hides_fairy_or_reuses_an_inside_sample() {
        let backend = include_str!("windows_backend.rs");
        let production = backend.split("#[cfg(test)]").next().unwrap_or(backend);
        for required in [
            "DWMWA_USE_HOSTBACKDROPBRUSH",
            "CreateHostBackdropBrush()",
            "CreateCompositionSurfaceForSwapChain(swap_chain)",
            "capture_source_valid: f32::from(edge_capture_ready)",
            "EDGE_CAPTURE_REBASE_FRAMES",
            "capture.rebase_after_drag()",
        ] {
            assert!(
                production.contains(required),
                "missing Host Backdrop contract: {required}"
            );
        }
        for forbidden in [
            "self.clean_backdrop.update(",
            "self.overlay_history",
            "sample_for_capture",
            "DwmFlush()",
            "CreateForMonitor",
            "ColorFormat::",
            "set_window_capture_excluded(hwnd.0 as isize, true)",
        ] {
            assert!(
                !production.contains(forbidden),
                "recursive capture path returned: {forbidden}"
            );
        }
    }

    #[test]
    fn native_drag_keeps_host_backdrop_live_and_rebases_only_the_edge_texture() {
        let backend = include_str!("windows_backend.rs");
        let production = backend.split("#[cfg(test)]").next().unwrap_or(backend);
        let host = include_str!("../lib.rs");
        for required in [
            "control.acknowledge_drag_mode()",
            "let drag_active = control.is_drag_active()",
            ".update_lens(presentation, drag, render_frame)",
            "renderer.rebase_after_drag()",
            "capture_source_valid: f32::from(edge_capture_ready)",
            "self.resume_after_generation",
        ] {
            assert!(
                production.contains(required),
                "missing drag contract: {required}"
            );
        }
        assert!(!production.contains("if control.drag_paused"));
        assert!(!production.contains("capture_source_staleness"));
        assert!(!production.contains("window_source_stale"));
        assert!(!production.contains("previous_overlay"));
        assert!(host.contains("state.native_gpu.set_drag_active(true)"));
        assert!(host.contains("set_drag_active(false)"));
    }

    #[test]
    fn material_shader_keeps_identity_above_the_refracted_edge() {
        let shader = include_str!("liquid_glass.hlsl");
        for required in [
            "EDGE_LENS_DEPTH_PX = 32.0",
            "HostBackdropBrush supplies the identity center",
            "Texture2D<float4> desktop_texture",
            "float optical_alpha",
            "float3 optical_premultiplied",
            "float edge_alpha = lerp(0.16, 0.24, material_opacity)",
            "float inner_dark_line",
            "float key_highlight",
            "float fill_highlight",
            "float atmosphere_outer",
            "float atmosphere_inner",
            "float3 identity_color",
            "float atmosphere_layer_alpha",
            "float beacon_layer_alpha",
            "float identity_alpha = 1.0",
            "float3 identity_premultiplied",
            "float3 foreground_premultiplied = identity_premultiplied",
            "composed_material * (1.0 - identity_alpha)",
        ] {
            assert!(
                shader.contains(required),
                "missing optical contract: {required}"
            );
        }
        assert!(!shader.contains("core_ring_outer"));
        assert!(!shader.contains("core_ring_inner"));
        assert!(!shader.contains("edge_blur"));
        assert!(!shader.contains("previous_overlay"));
        assert!(!shader.contains("remove_previous_overlay"));
        assert!(!shader.contains("identity_color)) * identity_alpha"));
        assert!(!shader.contains("color += max(0.0.xxx, float3(0.86, 0.96, 1.0) - color)"));
    }

    #[test]
    fn desktop_duplication_is_optional_and_never_blocks_host_backdrop_present() {
        let backend = include_str!("windows_backend.rs");
        let production = backend.split("#[cfg(test)]").next().unwrap_or(backend);
        for required in [
            "host_backdrop_only",
            "desktop_edge_capture_failed",
            "self.edge_capture = None",
            "renderer.present(",
        ] {
            assert!(
                production.contains(required),
                "missing composite-monitor contract: {required}"
            );
        }
        assert!(!production.contains("on_frame_arrived"));
        assert!(production.contains("CreateShaderResourceView(&texture"));
    }

    #[test]
    fn source_handoff_presents_the_candidate_before_releasing_the_previous_surface() {
        let manager = include_str!("../presence_native_gpu.rs");
        let wait = manager
            .find("candidate.wait_for_first_present()")
            .expect("candidate first-frame gate");
        let replace = manager
            .find(".replace(candidate)")
            .expect("atomic session replacement");
        let cleanup = manager
            .find("previous.stop()")
            .expect("previous session cleanup");
        assert!(wait < replace);
        assert!(replace < cleanup);
        assert!(manager.contains("candidate.set_drag_active(true)"));
    }

    #[test]
    fn native_surface_is_not_shown_until_after_the_first_present() {
        let backend = include_str!("windows_backend.rs");
        let constructor = backend
            .split("fn present(")
            .next()
            .expect("renderer constructor");
        assert!(!constructor.contains("surface.show()?"));
        let present = backend
            .split("fn present(")
            .nth(1)
            .expect("renderer present");
        let present_call = present
            .find("self.swap_chain.Present")
            .expect("Present call");
        let show_call = present.find("self.surface.show()?").expect("show call");
        assert!(present_call < show_call);
    }

    #[test]
    fn manager_handoff_waits_for_two_presented_frames() {
        let backend = include_str!("windows_backend.rs");
        assert!(backend.contains("status.frames_presented >= 2"));
    }
}
