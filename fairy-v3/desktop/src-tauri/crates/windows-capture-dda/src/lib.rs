//! GPU-only DXGI Desktop Duplication primitives for Fairy.
//!
//! This is a deliberately small adaptation of `windows-capture` 2.0.0. It
//! never maps captured pixels to CPU memory and requires the caller to select
//! the monitor output and own the D3D11 device used by the renderer.

use windows::core::Interface;
use windows::Win32::Foundation::{HMODULE, LUID, RECT};
use windows::Win32::Graphics::Direct3D::{
    D3D_DRIVER_TYPE_UNKNOWN, D3D_FEATURE_LEVEL, D3D_FEATURE_LEVEL_10_0, D3D_FEATURE_LEVEL_10_1,
    D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_11_1,
};
use windows::Win32::Graphics::Direct3D11::{
    D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, ID3D11Texture2D,
    D3D11_CREATE_DEVICE_BGRA_SUPPORT, D3D11_SDK_VERSION, D3D11_TEXTURE2D_DESC,
};
use windows::Win32::Graphics::Dxgi::Common::{
    DXGI_FORMAT, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_R10G10B10A2_UNORM,
    DXGI_FORMAT_R16G16B16A16_FLOAT,
};
use windows::Win32::Graphics::Dxgi::{
    CreateDXGIFactory1, IDXGIAdapter, IDXGIAdapter1, IDXGIFactory1, IDXGIOutput6,
    IDXGIOutputDuplication, DXGI_ERROR_ACCESS_LOST, DXGI_ERROR_NOT_FOUND, DXGI_ERROR_WAIT_TIMEOUT,
    DXGI_OUTDUPL_DESC, DXGI_OUTDUPL_FRAME_INFO,
};
use windows::Win32::Graphics::Gdi::HMONITOR;

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
    #[error("DDA_WINDOWS_ERROR: {0}")]
    Windows(#[from] windows::core::Error),
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
    monitor: HMONITOR,
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
                    monitor,
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
        self.monitor
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
    holding_frame: bool,
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
            holding_frame: false,
        })
    }

    pub fn recreate(&mut self) -> Result<(), DdaError> {
        if self.holding_frame {
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
    ) -> Result<AcquiredDesktopFrame<'_>, DdaError> {
        if self.holding_frame {
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
        self.holding_frame = true;
        let texture: ID3D11Texture2D = resource.ok_or(DdaError::DeviceUnavailable)?.cast()?;
        let mut texture_description = D3D11_TEXTURE2D_DESC::default();
        unsafe { texture.GetDesc(&mut texture_description) };
        Ok(AcquiredDesktopFrame {
            texture,
            texture_description,
            info,
            duplication: self.duplication.clone(),
            holding_frame: &mut self.holding_frame,
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
                self.holding_frame = false;
                Ok(())
            }
            Err(error) if error.code() == DXGI_ERROR_ACCESS_LOST => {
                self.holding_frame = false;
                Err(DdaError::AccessLost)
            }
            Err(error) => {
                self.holding_frame = false;
                Err(error.into())
            }
        }
    }
}

impl Drop for DuplicationSession {
    fn drop(&mut self) {
        if self.holding_frame {
            let _ = unsafe { self.duplication.ReleaseFrame() };
            self.holding_frame = false;
        }
    }
}

pub struct AcquiredDesktopFrame<'a> {
    texture: ID3D11Texture2D,
    texture_description: D3D11_TEXTURE2D_DESC,
    info: DXGI_OUTDUPL_FRAME_INFO,
    duplication: IDXGIOutputDuplication,
    holding_frame: &'a mut bool,
}

impl AcquiredDesktopFrame<'_> {
    pub fn texture(&self) -> &ID3D11Texture2D {
        &self.texture
    }

    pub const fn texture_description(&self) -> &D3D11_TEXTURE2D_DESC {
        &self.texture_description
    }

    pub const fn info(&self) -> &DXGI_OUTDUPL_FRAME_INFO {
        &self.info
    }
}

impl Drop for AcquiredDesktopFrame<'_> {
    fn drop(&mut self) {
        let _ = unsafe { self.duplication.ReleaseFrame() };
        *self.holding_frame = false;
    }
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
}
