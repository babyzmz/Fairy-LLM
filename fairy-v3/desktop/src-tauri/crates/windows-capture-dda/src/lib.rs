//! GPU-only DXGI Desktop Duplication primitives for Fairy.
//!
//! This is a deliberately small adaptation of `windows-capture` 2.0.0. It
//! never maps captured pixels to CPU memory and requires the caller to select
//! the monitor output and own the D3D11 device used by the renderer.

use std::ffi::c_void;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use windows::core::{s, w, Interface, BOOL};
use windows::Win32::Foundation::{GetLastError, E_ACCESSDENIED, HMODULE, HWND, LUID, RECT};
use windows::Win32::Graphics::Direct3D::{
    D3D_DRIVER_TYPE_UNKNOWN, D3D_FEATURE_LEVEL, D3D_FEATURE_LEVEL_10_0, D3D_FEATURE_LEVEL_10_1,
    D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_11_1,
};
use windows::Win32::Graphics::Direct3D11::{
    D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, ID3D11Resource, ID3D11Texture2D,
    D3D11_BIND_SHADER_RESOURCE, D3D11_BOX, D3D11_CREATE_DEVICE_BGRA_SUPPORT, D3D11_SDK_VERSION,
    D3D11_TEXTURE2D_DESC, D3D11_USAGE_DEFAULT,
};
use windows::Win32::Graphics::Dxgi::Common::{
    DXGI_FORMAT, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_R10G10B10A2_UNORM,
    DXGI_FORMAT_R16G16B16A16_FLOAT,
};
use windows::Win32::Graphics::Dxgi::{
    CreateDXGIFactory1, IDXGIAdapter, IDXGIAdapter1, IDXGIFactory1, IDXGIOutput6,
    IDXGIOutputDuplication, DXGI_ERROR_ACCESS_LOST, DXGI_ERROR_NOT_FOUND, DXGI_ERROR_WAIT_TIMEOUT,
    DXGI_OUTDUPL_DESC, DXGI_OUTDUPL_FRAME_INFO, DXGI_OUTDUPL_MOVE_RECT,
};
use windows::Win32::Graphics::Gdi::HMONITOR;
use windows::Win32::System::LibraryLoader::{GetModuleHandleW, GetProcAddress};

const WCA_EXCLUDED_FROM_DDA: i32 = 24;
const MAX_ACCESS_LOST_RETRIES: u32 = 6;

#[repr(C)]
struct WindowCompositionAttributeData {
    attribute: i32,
    data: *mut c_void,
    size: usize,
}

type SetWindowCompositionAttribute =
    unsafe extern "system" fn(HWND, *mut WindowCompositionAttributeData) -> BOOL;

#[derive(Debug, thiserror::Error)]
pub enum DdaError {
    #[error("DDA_OUTPUT_NOT_FOUND")]
    OutputNotFound,
    #[error("DDA_FRAME_TIMEOUT")]
    Timeout,
    #[error("DDA_ACCESS_LOST")]
    AccessLost,
    #[error("DDA_FRAME_ALREADY_HELD")]
    FrameAlreadyHeld,
    #[error("DDA_DEVICE_UNAVAILABLE")]
    DeviceUnavailable,
    #[error("DDA_CONTEXT_UNAVAILABLE")]
    ContextUnavailable,
    #[error("DDA_FEATURE_LEVEL_UNSUPPORTED")]
    FeatureLevelUnsupported,
    #[error("DDA_EXCLUSION_API_UNAVAILABLE")]
    ExclusionApiUnavailable,
    #[error("DDA_EXCLUSION_FAILED: {0}")]
    ExclusionFailed(u32),
    #[error("DDA_RECOVERY_EXHAUSTED")]
    RecoveryExhausted,
    #[error("DDA_WINDOWS_ERROR: {0}")]
    Windows(#[from] windows::core::Error),
}

#[derive(Clone, Debug)]
pub struct DesktopTextureFrameMetadata {
    pub accumulated_frames: u32,
    pub dirty_rect_count: u32,
    pub move_rect_count: u32,
    pub copied_full_frame: bool,
    pub source_format: DuplicationFormat,
    pub acquired_at: Instant,
}

#[derive(Clone, Debug)]
pub enum DesktopTexturePoll {
    NoFrame,
    Recovering { attempt: u32, retry_after: Duration },
    Updated(DesktopTextureFrameMetadata),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum DesktopFrameCopyPlan {
    NoPixelUpdate,
    FullFrame,
    Rectangles,
}

/// Retains the current desktop image in a shader-readable GPU texture.
/// Captured pixels never leave the D3D11 device.
pub struct DesktopTextureSource {
    binding: DisplayOutputBinding,
    session: DuplicationSession,
    device: ID3D11Device,
    context: ID3D11DeviceContext,
    retained_texture: Option<ID3D11Texture2D>,
    texture_generation: u64,
    force_full_copy: bool,
    access_lost_count: u32,
    recovery_attempt: u32,
    retry_at: Option<Instant>,
    last_frame: Option<DesktopTextureFrameMetadata>,
}

/// Excludes one HWND from DXGI Desktop Duplication while leaving ordinary
/// screen capture behavior unchanged. This is intentionally separate from
/// `SetWindowDisplayAffinity` and must be applied before the window is shown.
pub fn set_window_excluded_from_dda(hwnd: HWND, excluded: bool) -> Result<(), DdaError> {
    let module = unsafe { GetModuleHandleW(w!("user32.dll"))? };
    let procedure = unsafe { GetProcAddress(module, s!("SetWindowCompositionAttribute")) }
        .ok_or(DdaError::ExclusionApiUnavailable)?;
    let set_attribute: SetWindowCompositionAttribute = unsafe { std::mem::transmute(procedure) };
    let mut value = BOOL::from(excluded);
    let mut data = WindowCompositionAttributeData {
        attribute: WCA_EXCLUDED_FROM_DDA,
        data: (&mut value as *mut BOOL).cast(),
        size: std::mem::size_of::<BOOL>(),
    };
    if unsafe { set_attribute(hwnd, &mut data) }.as_bool() {
        Ok(())
    } else {
        Err(DdaError::ExclusionFailed(unsafe { GetLastError().0 }))
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DuplicationFormat {
    Bgra8,
    Rgb10A2,
    Rgba16F,
}

impl DuplicationFormat {
    pub const fn dxgi(self) -> DXGI_FORMAT {
        match self {
            Self::Bgra8 => DXGI_FORMAT_B8G8R8A8_UNORM,
            Self::Rgb10A2 => DXGI_FORMAT_R10G10B10A2_UNORM,
            Self::Rgba16F => DXGI_FORMAT_R16G16B16A16_FLOAT,
        }
    }

    pub const fn name(self) -> &'static str {
        match self {
            Self::Bgra8 => "bgra8",
            Self::Rgb10A2 => "rgb10a2",
            Self::Rgba16F => "rgba16f",
        }
    }

    pub fn from_dxgi(format: DXGI_FORMAT) -> Option<Self> {
        match format {
            DXGI_FORMAT_B8G8R8A8_UNORM => Some(Self::Bgra8),
            DXGI_FORMAT_R10G10B10A2_UNORM => Some(Self::Rgb10A2),
            DXGI_FORMAT_R16G16B16A16_FLOAT => Some(Self::Rgba16F),
            _ => None,
        }
    }
}

#[derive(Clone)]
pub struct DisplayOutputBinding {
    adapter: IDXGIAdapter1,
    output: IDXGIOutput6,
    monitor: isize,
    adapter_index: u32,
    output_index: u32,
    adapter_luid: LUID,
    adapter_name: String,
    output_name: String,
    desktop_coordinates: RECT,
}

impl DisplayOutputBinding {
    pub fn for_monitor(monitor: HMONITOR) -> Result<Self, DdaError> {
        let factory: IDXGIFactory1 = unsafe { CreateDXGIFactory1()? };
        for adapter_index in 0..64 {
            let adapter = match unsafe { factory.EnumAdapters1(adapter_index) } {
                Ok(adapter) => adapter,
                Err(error) if error.code() == DXGI_ERROR_NOT_FOUND => break,
                Err(error) => return Err(error.into()),
            };
            let adapter_description = unsafe { adapter.GetDesc1()? };
            for output_index in 0..64 {
                let output = match unsafe { adapter.EnumOutputs(output_index) } {
                    Ok(output) => output,
                    Err(error) if error.code() == DXGI_ERROR_NOT_FOUND => break,
                    Err(error) => return Err(error.into()),
                };
                let description = unsafe { output.GetDesc()? };
                if description.Monitor != monitor {
                    continue;
                }
                let output: IDXGIOutput6 = output.cast()?;
                return Ok(Self {
                    adapter,
                    output,
                    monitor: monitor.0 as isize,
                    adapter_index,
                    output_index,
                    adapter_luid: adapter_description.AdapterLuid,
                    adapter_name: utf16z(&adapter_description.Description),
                    output_name: utf16z(&description.DeviceName),
                    desktop_coordinates: description.DesktopCoordinates,
                });
            }
        }
        Err(DdaError::OutputNotFound)
    }

    pub fn adapter(&self) -> &IDXGIAdapter1 {
        &self.adapter
    }

    pub fn output(&self) -> &IDXGIOutput6 {
        &self.output
    }

    pub const fn monitor(&self) -> HMONITOR {
        HMONITOR(self.monitor as *mut c_void)
    }

    pub const fn adapter_index(&self) -> u32 {
        self.adapter_index
    }

    pub const fn output_index(&self) -> u32 {
        self.output_index
    }

    pub const fn adapter_luid(&self) -> LUID {
        self.adapter_luid
    }

    pub fn adapter_luid_string(&self) -> String {
        format!(
            "{:08X}:{:08X}",
            self.adapter_luid.HighPart as u32, self.adapter_luid.LowPart
        )
    }

    pub fn adapter_name(&self) -> &str {
        &self.adapter_name
    }

    pub fn output_name(&self) -> &str {
        &self.output_name
    }

    pub const fn desktop_coordinates(&self) -> RECT {
        self.desktop_coordinates
    }
}

pub fn create_device_for_output(
    binding: &DisplayOutputBinding,
) -> Result<(ID3D11Device, ID3D11DeviceContext), DdaError> {
    let adapter: IDXGIAdapter = binding.adapter().cast()?;
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
            &adapter,
            D3D_DRIVER_TYPE_UNKNOWN,
            HMODULE::default(),
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            Some(&feature_levels),
            D3D11_SDK_VERSION,
            Some(&mut device),
            Some(&mut feature_level),
            Some(&mut context),
        )?;
    }
    if feature_level.0 < D3D_FEATURE_LEVEL_10_0.0 {
        return Err(DdaError::FeatureLevelUnsupported);
    }
    Ok((
        device.ok_or(DdaError::DeviceUnavailable)?,
        context.ok_or(DdaError::ContextUnavailable)?,
    ))
}

pub struct DuplicationSession {
    device: ID3D11Device,
    context: ID3D11DeviceContext,
    output: IDXGIOutput6,
    duplication: IDXGIOutputDuplication,
    description: DXGI_OUTDUPL_DESC,
    formats: Vec<DXGI_FORMAT>,
    holding_frame: Arc<AtomicBool>,
}

impl DuplicationSession {
    pub fn new_with_device_output(
        device: ID3D11Device,
        context: ID3D11DeviceContext,
        output: IDXGIOutput6,
        supported_formats: &[DuplicationFormat],
    ) -> Result<Self, DdaError> {
        let formats = normalized_formats(supported_formats);
        let duplication = unsafe { output.DuplicateOutput1(&device, 0, &formats)? };
        let description = unsafe { duplication.GetDesc() };
        Ok(Self {
            device,
            context,
            output,
            duplication,
            description,
            formats,
            holding_frame: Arc::new(AtomicBool::new(false)),
        })
    }

    pub fn recreate(&mut self) -> Result<(), DdaError> {
        if self.holding_frame.load(Ordering::Acquire) {
            self.release_held_frame()?;
        }
        self.duplication = unsafe {
            self.output
                .DuplicateOutput1(&self.device, 0, &self.formats)?
        };
        self.description = unsafe { self.duplication.GetDesc() };
        Ok(())
    }

    pub fn acquire_next_frame(
        &mut self,
        timeout_ms: u32,
    ) -> Result<AcquiredDesktopFrame, DdaError> {
        if self.holding_frame.load(Ordering::Acquire) {
            return Err(DdaError::FrameAlreadyHeld);
        }
        let mut info = DXGI_OUTDUPL_FRAME_INFO::default();
        let mut resource = None;
        match unsafe {
            self.duplication
                .AcquireNextFrame(timeout_ms, &mut info, &mut resource)
        } {
            Ok(()) => {}
            Err(error) if error.code() == DXGI_ERROR_WAIT_TIMEOUT => return Err(DdaError::Timeout),
            Err(error) if error.code() == DXGI_ERROR_ACCESS_LOST => {
                return Err(DdaError::AccessLost)
            }
            Err(error) => return Err(error.into()),
        }
        self.holding_frame.store(true, Ordering::Release);
        let texture: ID3D11Texture2D = resource.ok_or(DdaError::DeviceUnavailable)?.cast()?;
        let mut texture_description = D3D11_TEXTURE2D_DESC::default();
        unsafe { texture.GetDesc(&mut texture_description) };
        Ok(AcquiredDesktopFrame {
            texture,
            texture_description,
            info,
            duplication: self.duplication.clone(),
            holding_frame: Arc::clone(&self.holding_frame),
        })
    }

    pub fn device(&self) -> &ID3D11Device {
        &self.device
    }

    pub fn context(&self) -> &ID3D11DeviceContext {
        &self.context
    }

    pub const fn description(&self) -> &DXGI_OUTDUPL_DESC {
        &self.description
    }

    pub fn format(&self) -> Option<DuplicationFormat> {
        DuplicationFormat::from_dxgi(self.description.ModeDesc.Format)
    }

    fn release_held_frame(&mut self) -> Result<(), DdaError> {
        match unsafe { self.duplication.ReleaseFrame() } {
            Ok(()) => {
                self.holding_frame.store(false, Ordering::Release);
                Ok(())
            }
            Err(error) if error.code() == DXGI_ERROR_ACCESS_LOST => {
                self.holding_frame.store(false, Ordering::Release);
                Err(DdaError::AccessLost)
            }
            Err(error) => {
                self.holding_frame.store(false, Ordering::Release);
                Err(error.into())
            }
        }
    }
}

impl Drop for DuplicationSession {
    fn drop(&mut self) {
        if self.holding_frame.load(Ordering::Acquire) {
            let _ = unsafe { self.duplication.ReleaseFrame() };
            self.holding_frame.store(false, Ordering::Release);
        }
    }
}

impl DesktopTextureSource {
    pub fn new_with_device_output(
        binding: DisplayOutputBinding,
        device: ID3D11Device,
        context: ID3D11DeviceContext,
    ) -> Result<Self, DdaError> {
        let session = DuplicationSession::new_with_device_output(
            device.clone(),
            context.clone(),
            binding.output().clone(),
            &[
                DuplicationFormat::Bgra8,
                DuplicationFormat::Rgb10A2,
                DuplicationFormat::Rgba16F,
            ],
        )?;
        Ok(Self {
            binding,
            session,
            device,
            context,
            retained_texture: None,
            texture_generation: 0,
            force_full_copy: true,
            access_lost_count: 0,
            recovery_attempt: 0,
            retry_at: None,
            last_frame: None,
        })
    }

    pub fn poll(&mut self, timeout_ms: u32) -> Result<DesktopTexturePoll, DdaError> {
        if let Some(retry_at) = self.retry_at {
            let now = Instant::now();
            if now < retry_at {
                return Ok(DesktopTexturePoll::Recovering {
                    attempt: self.recovery_attempt,
                    retry_after: retry_at.duration_since(now),
                });
            }
            match self.session.recreate() {
                Ok(()) => {
                    self.retry_at = None;
                    self.force_full_copy = true;
                }
                Err(error) if recoverable_duplication_error(&error) => {
                    return self.schedule_recovery()
                }
                Err(error) => return Err(error),
            }
        }

        let frame = match self.session.acquire_next_frame(timeout_ms) {
            Ok(frame) => frame,
            Err(DdaError::Timeout) => return Ok(DesktopTexturePoll::NoFrame),
            Err(error) if recoverable_duplication_error(&error) => return self.schedule_recovery(),
            Err(error) => return Err(error),
        };
        // LastPresentTime is zero for pointer-only updates. Dirty and move rectangles are not
        // available until the metadata buffer is read below, so do not run the complete copy
        // planner here or a valid single-frame desktop change would be discarded prematurely.
        if frame.info().LastPresentTime == 0 {
            drop(frame);
            return Ok(DesktopTexturePoll::NoFrame);
        }
        let description = *frame.texture_description();
        let source_format =
            DuplicationFormat::from_dxgi(description.Format).ok_or(DdaError::DeviceUnavailable)?;
        let texture_changed = self.retained_texture.as_ref().is_none_or(|texture| {
            let mut retained_description = D3D11_TEXTURE2D_DESC::default();
            unsafe { texture.GetDesc(&mut retained_description) };
            retained_description.Width != description.Width
                || retained_description.Height != description.Height
                || retained_description.Format != description.Format
        });
        if texture_changed {
            self.retained_texture = Some(create_retained_texture(&self.device, description)?);
            self.texture_generation = self.texture_generation.saturating_add(1);
            self.force_full_copy = true;
        }

        let dirty_rects = frame.dirty_rects()?;
        let move_rects = frame.move_rects()?;
        let retained = self
            .retained_texture
            .as_ref()
            .ok_or(DdaError::DeviceUnavailable)?;
        let retained_resource: ID3D11Resource = retained.cast()?;
        let source_resource: ID3D11Resource = frame.texture().cast()?;
        let copy_plan = desktop_frame_copy_plan(
            self.force_full_copy,
            frame.info().LastPresentTime,
            frame.info().AccumulatedFrames,
            dirty_rects.len(),
            move_rects.len(),
        );
        if copy_plan == DesktopFrameCopyPlan::NoPixelUpdate {
            drop(frame);
            return Ok(DesktopTexturePoll::NoFrame);
        }
        let copied_full_frame = copy_plan == DesktopFrameCopyPlan::FullFrame;
        match copy_plan {
            DesktopFrameCopyPlan::FullFrame => unsafe {
                self.context
                    .CopyResource(&retained_resource, &source_resource)
            },
            DesktopFrameCopyPlan::Rectangles => {
                for rectangle in dirty_rects
                    .iter()
                    .copied()
                    .chain(move_rects.iter().map(|rectangle| rectangle.DestinationRect))
                {
                    copy_current_rectangle(
                        &self.context,
                        &retained_resource,
                        &source_resource,
                        rectangle,
                        description.Width,
                        description.Height,
                    );
                }
            }
            DesktopFrameCopyPlan::NoPixelUpdate => unreachable!(
                "frames without changed desktop pixels are released before texture copying"
            ),
        }
        self.force_full_copy = false;
        self.recovery_attempt = 0;
        self.retry_at = None;
        let metadata = DesktopTextureFrameMetadata {
            accumulated_frames: frame.info().AccumulatedFrames,
            dirty_rect_count: dirty_rects.len().try_into().unwrap_or(u32::MAX),
            move_rect_count: move_rects.len().try_into().unwrap_or(u32::MAX),
            copied_full_frame,
            source_format,
            acquired_at: Instant::now(),
        };
        drop(frame);
        self.last_frame = Some(metadata.clone());
        Ok(DesktopTexturePoll::Updated(metadata))
    }

    pub fn texture(&self) -> Option<&ID3D11Texture2D> {
        self.retained_texture.as_ref()
    }

    pub const fn texture_generation(&self) -> u64 {
        self.texture_generation
    }

    pub const fn binding(&self) -> &DisplayOutputBinding {
        &self.binding
    }

    pub const fn access_lost_count(&self) -> u32 {
        self.access_lost_count
    }

    pub const fn rotation(&self) -> i32 {
        self.session.description().Rotation.0
    }

    pub fn frame_age(&self) -> Option<Duration> {
        self.last_frame
            .as_ref()
            .map(|frame| frame.acquired_at.elapsed())
    }

    pub fn last_frame(&self) -> Option<&DesktopTextureFrameMetadata> {
        self.last_frame.as_ref()
    }

    fn schedule_recovery(&mut self) -> Result<DesktopTexturePoll, DdaError> {
        self.access_lost_count = self.access_lost_count.saturating_add(1);
        self.recovery_attempt = self.recovery_attempt.saturating_add(1);
        if self.recovery_attempt > MAX_ACCESS_LOST_RETRIES {
            return Err(DdaError::RecoveryExhausted);
        }
        let delay = recovery_delay(self.recovery_attempt);
        self.retry_at = Some(Instant::now() + delay);
        Ok(DesktopTexturePoll::Recovering {
            attempt: self.recovery_attempt,
            retry_after: delay,
        })
    }
}

fn desktop_frame_copy_plan(
    force_full_copy: bool,
    last_present_time: i64,
    accumulated_frames: u32,
    dirty_rect_count: usize,
    move_rect_count: usize,
) -> DesktopFrameCopyPlan {
    if last_present_time == 0 {
        return DesktopFrameCopyPlan::NoPixelUpdate;
    }
    if force_full_copy || accumulated_frames > 1 || (dirty_rect_count == 0 && move_rect_count == 0)
    {
        DesktopFrameCopyPlan::FullFrame
    } else {
        DesktopFrameCopyPlan::Rectangles
    }
}

fn recoverable_duplication_error(error: &DdaError) -> bool {
    match error {
        DdaError::AccessLost => true,
        DdaError::Windows(error) => error.code() == E_ACCESSDENIED,
        _ => false,
    }
}

fn create_retained_texture(
    device: &ID3D11Device,
    source: D3D11_TEXTURE2D_DESC,
) -> Result<ID3D11Texture2D, DdaError> {
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
    unsafe { device.CreateTexture2D(&description, None, Some(&mut texture))? };
    texture.ok_or(DdaError::DeviceUnavailable)
}

fn copy_current_rectangle(
    context: &ID3D11DeviceContext,
    destination: &ID3D11Resource,
    source: &ID3D11Resource,
    rectangle: RECT,
    width: u32,
    height: u32,
) {
    let Some(rectangle) = normalized_rectangle(rectangle, width, height) else {
        return;
    };
    let left = rectangle.left as u32;
    let top = rectangle.top as u32;
    let source_box = D3D11_BOX {
        left,
        top,
        front: 0,
        right: rectangle.right as u32,
        bottom: rectangle.bottom as u32,
        back: 1,
    };
    unsafe {
        context.CopySubresourceRegion(
            Some(destination),
            0,
            left,
            top,
            0,
            Some(source),
            0,
            Some(&source_box),
        );
    }
}

fn normalized_rectangle(rectangle: RECT, width: u32, height: u32) -> Option<RECT> {
    let right_limit = i32::try_from(width).unwrap_or(i32::MAX);
    let bottom_limit = i32::try_from(height).unwrap_or(i32::MAX);
    let result = RECT {
        left: rectangle.left.clamp(0, right_limit),
        top: rectangle.top.clamp(0, bottom_limit),
        right: rectangle.right.clamp(0, right_limit),
        bottom: rectangle.bottom.clamp(0, bottom_limit),
    };
    (result.right > result.left && result.bottom > result.top).then_some(result)
}

fn recovery_delay(attempt: u32) -> Duration {
    let exponent = attempt.saturating_sub(1).min(5);
    Duration::from_millis(50_u64.saturating_mul(1_u64 << exponent))
}

pub struct AcquiredDesktopFrame {
    texture: ID3D11Texture2D,
    texture_description: D3D11_TEXTURE2D_DESC,
    info: DXGI_OUTDUPL_FRAME_INFO,
    duplication: IDXGIOutputDuplication,
    holding_frame: Arc<AtomicBool>,
}

impl AcquiredDesktopFrame {
    pub fn texture(&self) -> &ID3D11Texture2D {
        &self.texture
    }

    pub const fn texture_description(&self) -> &D3D11_TEXTURE2D_DESC {
        &self.texture_description
    }

    pub const fn info(&self) -> &DXGI_OUTDUPL_FRAME_INFO {
        &self.info
    }

    pub fn dirty_rects(&self) -> Result<Vec<RECT>, DdaError> {
        metadata_rectangles(
            self.info.TotalMetadataBufferSize,
            |size, buffer, required| unsafe {
                self.duplication.GetFrameDirtyRects(size, buffer, required)
            },
        )
    }

    pub fn move_rects(&self) -> Result<Vec<DXGI_OUTDUPL_MOVE_RECT>, DdaError> {
        metadata_rectangles(
            self.info.TotalMetadataBufferSize,
            |size, buffer, required| unsafe {
                self.duplication.GetFrameMoveRects(size, buffer, required)
            },
        )
    }
}

impl Drop for AcquiredDesktopFrame {
    fn drop(&mut self) {
        let _ = unsafe { self.duplication.ReleaseFrame() };
        self.holding_frame.store(false, Ordering::Release);
    }
}

fn metadata_rectangles<T: Default + Clone>(
    metadata_size: u32,
    read: impl FnOnce(u32, *mut T, *mut u32) -> windows::core::Result<()>,
) -> Result<Vec<T>, DdaError> {
    if metadata_size == 0 {
        return Ok(Vec::new());
    }
    let item_size = std::mem::size_of::<T>();
    let capacity = (metadata_size as usize / item_size).saturating_add(1);
    let mut values = vec![T::default(); capacity];
    let buffer_size = u32::try_from(values.len().saturating_mul(item_size)).unwrap_or(u32::MAX);
    let mut required = 0;
    read(buffer_size, values.as_mut_ptr(), &mut required)?;
    values.truncate(required as usize / item_size);
    Ok(values)
}

fn normalized_formats(formats: &[DuplicationFormat]) -> Vec<DXGI_FORMAT> {
    let mut result = Vec::with_capacity(formats.len() + 1);
    for format in formats.iter().map(|format| format.dxgi()) {
        if !result.contains(&format) {
            result.push(format);
        }
    }
    if !result.contains(&DXGI_FORMAT_B8G8R8A8_UNORM) {
        result.push(DXGI_FORMAT_B8G8R8A8_UNORM);
    }
    result
}

fn utf16z(value: &[u16]) -> String {
    let length = value
        .iter()
        .position(|character| *character == 0)
        .unwrap_or(value.len());
    String::from_utf16_lossy(&value[..length])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bgra8_is_always_available_as_a_fallback() {
        let formats = normalized_formats(&[DuplicationFormat::Rgba16F]);
        assert_eq!(formats[0], DXGI_FORMAT_R16G16B16A16_FLOAT);
        assert_eq!(formats[1], DXGI_FORMAT_B8G8R8A8_UNORM);
    }

    #[test]
    fn duplicate_formats_are_removed_without_reordering() {
        let formats = normalized_formats(&[
            DuplicationFormat::Bgra8,
            DuplicationFormat::Rgba16F,
            DuplicationFormat::Bgra8,
        ]);
        assert_eq!(
            formats,
            vec![DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_R16G16B16A16_FLOAT,]
        );
    }

    #[test]
    fn dirty_rectangles_are_clamped_and_empty_rectangles_are_dropped() {
        assert_eq!(
            normalized_rectangle(
                RECT {
                    left: -5,
                    top: 4,
                    right: 120,
                    bottom: 80,
                },
                100,
                60,
            ),
            Some(RECT {
                left: 0,
                top: 4,
                right: 100,
                bottom: 60,
            })
        );
        assert_eq!(
            normalized_rectangle(
                RECT {
                    left: 200,
                    top: 0,
                    right: 220,
                    bottom: 20,
                },
                100,
                60,
            ),
            None
        );
    }

    #[test]
    fn access_lost_recovery_uses_bounded_exponential_backoff() {
        assert_eq!(recovery_delay(1), Duration::from_millis(50));
        assert_eq!(recovery_delay(2), Duration::from_millis(100));
        assert_eq!(recovery_delay(6), Duration::from_millis(1_600));
        assert_eq!(recovery_delay(99), Duration::from_millis(1_600));
    }

    #[test]
    fn access_denied_during_desktop_switch_uses_the_same_bounded_recovery() {
        assert!(recoverable_duplication_error(&DdaError::AccessLost));
        assert!(recoverable_duplication_error(&DdaError::Windows(
            windows::core::Error::from_hresult(E_ACCESSDENIED),
        )));
        assert!(!recoverable_duplication_error(&DdaError::DeviceUnavailable));
    }

    #[test]
    fn pointer_only_frames_do_not_wake_the_compositor() {
        assert_eq!(
            desktop_frame_copy_plan(true, 0, 1, 0, 0),
            DesktopFrameCopyPlan::NoPixelUpdate
        );
        assert_eq!(
            desktop_frame_copy_plan(false, 0, 1, 4, 0),
            DesktopFrameCopyPlan::NoPixelUpdate
        );
    }

    #[test]
    fn copy_plan_uses_dirty_pixels_and_full_copies_unannotated_presents() {
        assert_eq!(
            desktop_frame_copy_plan(true, 42, 1, 0, 0),
            DesktopFrameCopyPlan::FullFrame
        );
        assert_eq!(
            desktop_frame_copy_plan(false, 42, 1, 0, 0),
            DesktopFrameCopyPlan::FullFrame
        );
        assert_eq!(
            desktop_frame_copy_plan(false, 42, 2, 1, 0),
            DesktopFrameCopyPlan::FullFrame
        );
        assert_eq!(
            desktop_frame_copy_plan(false, 42, 1, 1, 0),
            DesktopFrameCopyPlan::Rectangles
        );
    }
}
