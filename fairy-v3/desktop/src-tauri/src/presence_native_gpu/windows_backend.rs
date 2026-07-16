use std::cmp::Ordering;
use std::collections::VecDeque;
use std::ffi::c_void;
use std::sync::atomic::{AtomicIsize, Ordering as AtomicOrdering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use windows::core::{w, Interface, PCSTR, PCWSTR};
use windows::Win32::Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows::Win32::Graphics::Direct3D::Fxc::D3DCompile;
use windows::Win32::Graphics::Direct3D::{
    ID3DBlob, ID3DInclude, D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST,
};
use windows::Win32::Graphics::Direct3D11::{
    ID3D11Buffer, ID3D11Device, ID3D11DeviceContext, ID3D11PixelShader, ID3D11RenderTargetView,
    ID3D11SamplerState, ID3D11ShaderResourceView, ID3D11Texture2D, ID3D11VertexShader,
    D3D11_BIND_CONSTANT_BUFFER, D3D11_BUFFER_DESC, D3D11_COMPARISON_NEVER,
    D3D11_FILTER_MIN_MAG_MIP_LINEAR, D3D11_SAMPLER_DESC, D3D11_TEXTURE2D_DESC,
    D3D11_TEXTURE_ADDRESS_CLAMP, D3D11_USAGE_DEFAULT, D3D11_VIEWPORT,
};
use windows::Win32::Graphics::DirectComposition::{
    DCompositionCreateDevice, IDCompositionDevice, IDCompositionTarget, IDCompositionVisual,
};
use windows::Win32::Graphics::Dxgi::Common::{
    DXGI_ALPHA_MODE_PREMULTIPLIED, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_SAMPLE_DESC,
};
use windows::Win32::Graphics::Dxgi::{
    IDXGIDevice, IDXGIFactory2, IDXGISwapChain1, IDXGISwapChain3, DXGI_PRESENT,
    DXGI_SCALING_STRETCH, DXGI_SWAP_CHAIN_DESC1, DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL,
    DXGI_USAGE_RENDER_TARGET_OUTPUT,
};
use windows::Win32::Graphics::Gdi::{
    GetMonitorInfoW, MonitorFromPoint, HMONITOR, MONITORINFO, MONITOR_DEFAULTTONEAREST,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DestroyWindow, GetWindowRect, IsWindow, PostMessageW,
    RegisterClassW, SetWindowPos, ShowWindow, HTTRANSPARENT, SWP_NOACTIVATE, SWP_NOOWNERZORDER,
    SWP_NOSIZE, SW_SHOWNOACTIVATE, WM_CLOSE, WM_NCHITTEST, WNDCLASSW, WS_EX_NOACTIVATE,
    WS_EX_NOREDIRECTIONBITMAP, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_POPUP,
};
use windows_capture::capture::{CaptureControl, Context, GraphicsCaptureApiHandler};
use windows_capture::frame::Frame;
use windows_capture::graphics_capture_api::InternalCaptureControl;
use windows_capture::monitor::Monitor;
use windows_capture::settings::{
    ColorFormat, CursorCaptureSettings, DirtyRegionSettings, DrawBorderSettings,
    MinimumUpdateIntervalSettings, SecondaryWindowSettings, Settings,
};

use super::{
    NativeGpuBackend, NativeGpuConfig, NativeGpuError, NativeGpuLifecycle, NativeGpuStatus,
    NativeGpuVisualState,
};
use crate::presence_coordinator::PhysicalFrame;

const SHADER_SOURCE: &str = include_str!("liquid_glass.hlsl");
const SWAP_CHAIN_BUFFER_COUNT: u32 = 2;
const METRIC_WINDOW: usize = 600;
const NATIVE_SURFACE_CLASS: PCWSTR = w!("FairyNativePresenceRendererClass");
const NATIVE_SURFACE_TITLE: PCWSTR = w!("Fairy Native Presence Renderer");
static NATIVE_SURFACE_CLASS_REGISTERED: OnceLock<Result<(), String>> = OnceLock::new();

type NativeCaptureControl = CaptureControl<NativeCaptureHandler, String>;

pub(super) struct WindowsNativeGpuSession {
    control: NativeCaptureControl,
    surface_hwnd: Arc<AtomicIsize>,
}

impl WindowsNativeGpuSession {
    pub(super) fn start(
        config: NativeGpuConfig,
        status: Arc<Mutex<NativeGpuStatus>>,
    ) -> Result<Self, NativeGpuError> {
        let (monitor, monitor_frame) = capture_monitor(config.render_frame)?;
        let surface_hwnd = Arc::new(AtomicIsize::new(0));
        let flags = NativeCaptureFlags {
            config,
            monitor_frame,
            status,
            surface_hwnd: Arc::clone(&surface_hwnd),
        };
        let minimum_interval = Duration::from_secs_f64(1.0 / f64::from(config.target_frame_rate));
        let settings = Settings::new(
            monitor,
            CursorCaptureSettings::WithoutCursor,
            DrawBorderSettings::WithoutBorder,
            SecondaryWindowSettings::Exclude,
            MinimumUpdateIntervalSettings::Custom(minimum_interval),
            DirtyRegionSettings::Default,
            ColorFormat::Bgra8,
            flags,
        );
        let control = NativeCaptureHandler::start_free_threaded(settings)
            .map_err(|error| NativeGpuError::StartFailed(error.to_string()))?;
        Ok(Self {
            control,
            surface_hwnd,
        })
    }

    pub(super) fn stop(self) -> Result<(), NativeGpuError> {
        let raw_hwnd = self.surface_hwnd.load(AtomicOrdering::Acquire);
        if raw_hwnd != 0 {
            let hwnd = HWND(raw_hwnd as *mut c_void);
            let _ =
                unsafe { PostMessageW(Some(hwnd), WM_CLOSE, WPARAM::default(), LPARAM::default()) };
            let deadline = Instant::now() + Duration::from_millis(750);
            while unsafe { IsWindow(Some(hwnd)) }.as_bool() && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(5));
            }
        }
        self.control
            .stop()
            .map_err(|error| NativeGpuError::StopFailed(error.to_string()))
    }

    pub(super) fn prepare_visual_test(&self) -> Result<(), NativeGpuError> {
        self.control
            .halt_handle()
            .store(true, std::sync::atomic::Ordering::Release);
        std::thread::sleep(Duration::from_millis(50));
        let raw_hwnd = self.surface_hwnd.load(AtomicOrdering::Acquire);
        if raw_hwnd == 0 {
            return Err(NativeGpuError::NotRunning);
        }
        crate::presence_backdrop::set_window_capture_excluded(raw_hwnd, false)
            .map_err(NativeGpuError::StartFailed)
    }
}

#[derive(Clone)]
struct NativeCaptureFlags {
    config: NativeGpuConfig,
    monitor_frame: PhysicalFrame,
    status: Arc<Mutex<NativeGpuStatus>>,
    surface_hwnd: Arc<AtomicIsize>,
}

struct NativeCaptureHandler {
    renderer: NativeCompositionRenderer,
    status: Arc<Mutex<NativeGpuStatus>>,
    started: Instant,
    last_frame: Option<Instant>,
    frame_intervals_ms: VecDeque<f64>,
    callback_to_present_ms: VecDeque<f64>,
    present_ms: VecDeque<f64>,
    frames_presented: u64,
}

// windows-capture constructs and invokes this handler on one dedicated thread. The returned
// control owns an Arc to it, but this module never exposes or locks that callback from another
// thread; stop joins the capture thread before releasing the final COM references.
unsafe impl Send for NativeCaptureHandler {}

impl GraphicsCaptureApiHandler for NativeCaptureHandler {
    type Flags = NativeCaptureFlags;
    type Error = String;

    fn new(context: Context<Self::Flags>) -> Result<Self, Self::Error> {
        let renderer = NativeCompositionRenderer::new(
            context.device,
            context.device_context,
            context.flags.config,
            context.flags.monitor_frame,
            Arc::clone(&context.flags.surface_hwnd),
        )?;
        let started = Instant::now();
        update_status(&context.flags.status, |status| {
            status.backend = NativeGpuBackend::WindowsGraphicsCaptureD3d11DirectComposition;
            status.lifecycle = NativeGpuLifecycle::Running;
            status.zero_copy_capture = true;
            status.pixel_ipc = false;
            status.monitor_width = context.flags.monitor_frame.width;
            status.monitor_height = context.flags.monitor_frame.height;
            status.started_at_ms = now_ms();
            status.error_code = None;
        });
        Ok(Self {
            renderer,
            status: context.flags.status,
            started,
            last_frame: None,
            frame_intervals_ms: VecDeque::with_capacity(METRIC_WINDOW),
            callback_to_present_ms: VecDeque::with_capacity(METRIC_WINDOW),
            present_ms: VecDeque::with_capacity(METRIC_WINDOW),
            frames_presented: 0,
        })
    }

    fn on_frame_arrived(
        &mut self,
        frame: &mut Frame,
        _capture_control: InternalCaptureControl,
    ) -> Result<(), Self::Error> {
        let callback_started = Instant::now();
        if let Some(last) = self.last_frame.replace(callback_started) {
            push_metric(
                &mut self.frame_intervals_ms,
                callback_started.duration_since(last).as_secs_f64() * 1_000.0,
            );
        }
        let present_started = Instant::now();
        if let Err(error) = self
            .renderer
            .present(frame.as_raw_texture(), self.started.elapsed())
        {
            self.renderer.surface.close_on_owner_thread();
            update_status(&self.status, |status| {
                status.lifecycle = NativeGpuLifecycle::Failed;
                status.error_code = Some(error.clone());
            });
            return Err(error);
        }
        let presented = Instant::now();
        push_metric(
            &mut self.present_ms,
            presented.duration_since(present_started).as_secs_f64() * 1_000.0,
        );
        push_metric(
            &mut self.callback_to_present_ms,
            presented.duration_since(callback_started).as_secs_f64() * 1_000.0,
        );
        self.frames_presented = self.frames_presented.saturating_add(1);
        if self.frames_presented <= 3 || self.frames_presented.is_multiple_of(15) {
            self.publish_metrics();
        }
        Ok(())
    }

    fn on_closed(&mut self) -> Result<(), Self::Error> {
        self.renderer.surface.close_on_owner_thread();
        update_status(&self.status, |status| {
            status.lifecycle = NativeGpuLifecycle::Failed;
            status.error_code = Some("PRESENCE_NATIVE_GPU_CAPTURE_CLOSED".to_owned());
        });
        Ok(())
    }
}

impl NativeCaptureHandler {
    fn publish_metrics(&self) {
        let elapsed = self.started.elapsed().as_secs_f64();
        let frames = self.frames_presented;
        update_status(&self.status, |status| {
            status.frames_presented = frames;
            status.capture_fps_avg = if elapsed > 0.0 {
                frames as f64 / elapsed
            } else {
                0.0
            };
            status.frame_interval_p1_fps = percentile(&self.frame_intervals_ms, 0.99)
                .filter(|interval| *interval > 0.0)
                .map(|interval| 1_000.0 / interval)
                .unwrap_or(0.0);
            status.callback_to_present_p95_ms =
                percentile(&self.callback_to_present_ms, 0.95).unwrap_or(0.0);
            status.present_p95_ms = percentile(&self.present_ms, 0.95).unwrap_or(0.0);
            status.last_presented_at_ms = now_ms();
        });
    }
}

struct NativeCompositionSurface {
    hwnd: HWND,
    tracking_hwnd: HWND,
    input_hwnd: HWND,
    frame: PhysicalFrame,
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
        let hwnd = unsafe {
            CreateWindowExW(
                WS_EX_NOACTIVATE
                    | WS_EX_NOREDIRECTIONBITMAP
                    | WS_EX_TOOLWINDOW
                    | WS_EX_TOPMOST
                    | WS_EX_TRANSPARENT,
                NATIVE_SURFACE_CLASS,
                NATIVE_SURFACE_TITLE,
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
        if let Err(error) = crate::presence_backdrop::exclude_window_from_capture(hwnd.0 as isize) {
            let _ = unsafe { DestroyWindow(hwnd) };
            return Err(error);
        }
        published_hwnd.store(hwnd.0 as isize, AtomicOrdering::Release);
        Ok(Self {
            hwnd,
            tracking_hwnd: HWND(config.render_hwnd as *mut c_void),
            input_hwnd: HWND(config.input_hwnd as *mut c_void),
            frame: config.render_frame,
            creator_thread_id: unsafe { GetCurrentThreadId() },
            published_hwnd,
        })
    }

    fn show(&self) -> Result<(), String> {
        unsafe {
            SetWindowPos(
                self.hwnd,
                Some(self.input_hwnd),
                self.frame.x,
                self.frame.y,
                self.frame.width as i32,
                self.frame.height as i32,
                SWP_NOACTIVATE | SWP_NOOWNERZORDER,
            )
            .map_err(|error| windows_stage_error("WINDOW_POSITION", error))?;
            let _ = ShowWindow(self.hwnd, SW_SHOWNOACTIVATE);
        }
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
                    SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_NOSIZE,
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
        WM_CLOSE => {
            let _ = unsafe { DestroyWindow(hwnd) };
            LRESULT(0)
        }
        _ => unsafe { DefWindowProcW(hwnd, message, wparam, lparam) },
    }
}

struct NativeCompositionRenderer {
    surface: NativeCompositionSurface,
    device: ID3D11Device,
    context: ID3D11DeviceContext,
    swap_chain: IDXGISwapChain3,
    _composition_device: IDCompositionDevice,
    _composition_target: IDCompositionTarget,
    _composition_visual: IDCompositionVisual,
    render_targets: Vec<ID3D11RenderTargetView>,
    vertex_shader: ID3D11VertexShader,
    pixel_shader: ID3D11PixelShader,
    sampler: ID3D11SamplerState,
    constants: ID3D11Buffer,
    config: NativeGpuConfig,
    monitor_frame: PhysicalFrame,
    capture_views: Vec<CaptureView>,
}

struct CaptureView {
    texture_identity: usize,
    view: ID3D11ShaderResourceView,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct PresenceConstants {
    output_size: [f32; 2],
    capture_size: [f32; 2],
    source_origin_px: [f32; 2],
    elapsed_seconds: f32,
    surface_scale: f32,
    capsule_visible: f32,
    expansion_direction: f32,
    visual_state: f32,
    material_opacity: f32,
    reserved: [f32; 4],
}

impl NativeCompositionRenderer {
    fn new(
        device: ID3D11Device,
        context: ID3D11DeviceContext,
        config: NativeGpuConfig,
        monitor_frame: PhysicalFrame,
        surface_hwnd: Arc<AtomicIsize>,
    ) -> Result<Self, String> {
        let surface = NativeCompositionSurface::new(config, surface_hwnd)?;
        let swap_chain = create_swap_chain(&device, config.render_frame)?;
        let render_targets = create_render_targets(&device, &swap_chain)?;
        let (vertex_shader, pixel_shader) = create_shaders(&device)?;
        let sampler = create_sampler(&device)?;
        let constants = create_constant_buffer(&device)?;
        let dxgi_device: IDXGIDevice = device
            .cast()
            .map_err(|error| windows_stage_error("DCOMP_DXGI_DEVICE", error))?;
        let composition_device: IDCompositionDevice =
            unsafe { DCompositionCreateDevice(&dxgi_device) }
                .map_err(|error| windows_stage_error("DCOMP_CREATE_DEVICE", error))?;
        let composition_target =
            unsafe { composition_device.CreateTargetForHwnd(surface.hwnd, true) }
                .map_err(|error| windows_stage_error("DCOMP_CREATE_TARGET", error))?;
        let composition_visual = unsafe { composition_device.CreateVisual() }
            .map_err(|error| windows_stage_error("DCOMP_CREATE_VISUAL", error))?;
        let swap_chain_base: IDXGISwapChain1 = swap_chain
            .cast()
            .map_err(|error| windows_stage_error("DCOMP_SWAP_CHAIN_CAST", error))?;
        unsafe {
            composition_visual
                .SetContent(&swap_chain_base)
                .map_err(|error| windows_stage_error("DCOMP_SET_CONTENT", error))?;
            composition_target
                .SetRoot(&composition_visual)
                .map_err(|error| windows_stage_error("DCOMP_SET_ROOT", error))?;
            composition_device
                .Commit()
                .map_err(|error| windows_stage_error("DCOMP_COMMIT", error))?;
        }
        surface.show()?;
        Ok(Self {
            surface,
            device,
            context,
            swap_chain,
            _composition_device: composition_device,
            _composition_target: composition_target,
            _composition_visual: composition_visual,
            render_targets,
            vertex_shader,
            pixel_shader,
            sampler,
            constants,
            config,
            monitor_frame,
            capture_views: Vec::with_capacity(3),
        })
    }

    fn present(&mut self, texture: &ID3D11Texture2D, elapsed: Duration) -> Result<(), String> {
        let render_frame = self.surface.sync_to_tracking_window()?;
        let mut texture_desc = D3D11_TEXTURE2D_DESC::default();
        unsafe { texture.GetDesc(&mut texture_desc) };
        if texture_desc.Width == 0 || texture_desc.Height == 0 {
            return Err("PRESENCE_NATIVE_GPU_EMPTY_CAPTURE_TEXTURE".to_owned());
        }
        let capture_view = self.capture_view(texture)?;
        let constants = PresenceConstants {
            output_size: [render_frame.width as f32, render_frame.height as f32],
            capture_size: [texture_desc.Width as f32, texture_desc.Height as f32],
            source_origin_px: [
                render_frame.x.saturating_sub(self.monitor_frame.x) as f32,
                render_frame.y.saturating_sub(self.monitor_frame.y) as f32,
            ],
            elapsed_seconds: elapsed.as_secs_f32(),
            surface_scale: render_frame.width as f32 / 640.0,
            capsule_visible: f32::from(self.config.capsule_visible),
            expansion_direction: 1.0,
            visual_state: visual_state_value(self.config.visual_state),
            material_opacity: self.config.opacity,
            reserved: [diagnostic_solid_output(), 0.0, 0.0, 0.0],
        };
        let render_target = self
            .render_targets
            .first()
            .ok_or_else(|| "PRESENCE_NATIVE_GPU_BACK_BUFFER_UNAVAILABLE".to_owned())?;
        unsafe {
            self.context.UpdateSubresource(
                &self.constants,
                0,
                None,
                (&constants as *const PresenceConstants).cast(),
                0,
                0,
            );
            self.context.ClearRenderTargetView(render_target, &[0.0; 4]);
            self.context
                .OMSetRenderTargets(Some(&[Some(render_target.clone())]), None);
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
                .PSSetShaderResources(0, Some(&[Some(capture_view)]));
            self.context
                .PSSetSamplers(0, Some(&[Some(self.sampler.clone())]));
            self.context.Draw(3, 0);
            self.context.PSSetShaderResources(0, Some(&[None]));
        }
        let result = unsafe { self.swap_chain.Present(0, DXGI_PRESENT(0)) };
        result.ok().map_err(windows_error)
    }

    fn capture_view(
        &mut self,
        texture: &ID3D11Texture2D,
    ) -> Result<ID3D11ShaderResourceView, String> {
        let identity = texture.as_raw() as usize;
        if let Some(cached) = self
            .capture_views
            .iter()
            .find(|entry| entry.texture_identity == identity)
        {
            return Ok(cached.view.clone());
        }
        let mut view = None;
        unsafe {
            self.device
                .CreateShaderResourceView(texture, None, Some(&mut view))
                .map_err(windows_error)?;
        }
        let view = view.ok_or_else(|| "PRESENCE_NATIVE_GPU_CAPTURE_VIEW_UNAVAILABLE".to_owned())?;
        if self.capture_views.len() >= 4 {
            self.capture_views.remove(0);
        }
        self.capture_views.push(CaptureView {
            texture_identity: identity,
            view: view.clone(),
        });
        Ok(view)
    }
}

fn diagnostic_solid_output() -> f32 {
    if cfg!(debug_assertions) && std::env::var_os("FAIRY_PRESENCE_GPU_DIAGNOSTIC_SOLID").is_some() {
        1.0
    } else {
        0.0
    }
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

fn create_render_targets(
    device: &ID3D11Device,
    swap_chain: &IDXGISwapChain3,
) -> Result<Vec<ID3D11RenderTargetView>, String> {
    let base: IDXGISwapChain1 = swap_chain
        .cast()
        .map_err(|error| windows_stage_error("RENDER_TARGET_SWAP_CHAIN_CAST", error))?;
    let mut targets = Vec::with_capacity(1);
    for index in 0..1 {
        let texture: ID3D11Texture2D = unsafe { base.GetBuffer(index) }
            .map_err(|error| windows_stage_error("RENDER_TARGET_GET_BUFFER", error))?;
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
        targets.push(
            target.ok_or_else(|| "PRESENCE_NATIVE_GPU_RENDER_TARGET_UNAVAILABLE".to_owned())?,
        );
    }
    Ok(targets)
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
    let mut bytecode = None;
    let mut errors = None;
    let result = unsafe {
        D3DCompile(
            SHADER_SOURCE.as_ptr().cast(),
            SHADER_SOURCE.len(),
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
            "PRESENCE_NATIVE_GPU_SHADER_COMPILE_FAILED: {detail}"
        ));
    }
    bytecode.ok_or_else(|| "PRESENCE_NATIVE_GPU_SHADER_BYTECODE_UNAVAILABLE".to_owned())
}

fn blob_bytes(blob: &ID3DBlob) -> &[u8] {
    unsafe {
        std::slice::from_raw_parts(blob.GetBufferPointer().cast::<u8>(), blob.GetBufferSize())
    }
}

fn create_sampler(device: &ID3D11Device) -> Result<ID3D11SamplerState, String> {
    let description = D3D11_SAMPLER_DESC {
        Filter: D3D11_FILTER_MIN_MAG_MIP_LINEAR,
        AddressU: D3D11_TEXTURE_ADDRESS_CLAMP,
        AddressV: D3D11_TEXTURE_ADDRESS_CLAMP,
        AddressW: D3D11_TEXTURE_ADDRESS_CLAMP,
        MaxAnisotropy: 1,
        ComparisonFunc: D3D11_COMPARISON_NEVER,
        MinLOD: 0.0,
        MaxLOD: f32::MAX,
        ..D3D11_SAMPLER_DESC::default()
    };
    let mut sampler = None;
    unsafe {
        device
            .CreateSamplerState(&description, Some(&mut sampler))
            .map_err(|error| windows_stage_error("SAMPLER_CREATE", error))?;
    }
    sampler.ok_or_else(|| "PRESENCE_NATIVE_GPU_SAMPLER_UNAVAILABLE".to_owned())
}

fn create_constant_buffer(device: &ID3D11Device) -> Result<ID3D11Buffer, String> {
    let description = D3D11_BUFFER_DESC {
        ByteWidth: std::mem::size_of::<PresenceConstants>() as u32,
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
            .map_err(|error| windows_stage_error("CONSTANT_BUFFER_CREATE", error))?;
    }
    buffer.ok_or_else(|| "PRESENCE_NATIVE_GPU_CONSTANT_BUFFER_UNAVAILABLE".to_owned())
}

fn capture_monitor(frame: PhysicalFrame) -> Result<(Monitor, PhysicalFrame), NativeGpuError> {
    let center = POINT {
        x: frame.x.saturating_add((frame.width / 2) as i32),
        y: frame.y.saturating_add((frame.height / 2) as i32),
    };
    let handle = unsafe { MonitorFromPoint(center, MONITOR_DEFAULTTONEAREST) };
    if handle.is_invalid() {
        return Err(NativeGpuError::StartFailed(
            "PRESENCE_NATIVE_GPU_MONITOR_UNAVAILABLE".to_owned(),
        ));
    }
    let monitor_frame = monitor_frame(handle)?;
    let monitor = Monitor::from_raw_hmonitor(handle.0);
    Ok((monitor, monitor_frame))
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
        NativeGpuVisualState::Analyzing => 2.0,
        NativeGpuVisualState::Tool => 3.0,
        NativeGpuVisualState::Streaming => 4.0,
        NativeGpuVisualState::Speaking => 5.0,
        NativeGpuVisualState::Approval => 6.0,
        NativeGpuVisualState::Ready => 7.0,
        NativeGpuVisualState::Error => 8.0,
        NativeGpuVisualState::Sleeping => 9.0,
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentile_uses_the_requested_tail_without_panicking_on_empty_samples() {
        assert_eq!(percentile(&VecDeque::new(), 0.95), None);
        let samples = VecDeque::from([1.0, 2.0, 3.0, 4.0, 100.0]);
        assert_eq!(percentile(&samples, 0.95), Some(100.0));
        assert_eq!(percentile(&samples, 0.0), Some(1.0));
    }

    #[test]
    fn constant_buffer_remains_aligned_for_d3d11() {
        assert_eq!(std::mem::size_of::<PresenceConstants>() % 16, 0);
        assert_eq!(std::mem::size_of::<PresenceConstants>(), 64);
    }
}
