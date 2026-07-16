use std::sync::{Arc, Mutex};

use serde::{Deserialize, Serialize};

use crate::presence_coordinator::PhysicalFrame;

#[cfg(target_os = "windows")]
mod windows_backend;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum NativeGpuLifecycle {
    #[default]
    Idle,
    Starting,
    Running,
    Stopping,
    Failed,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum NativeGpuBackend {
    #[default]
    Unavailable,
    WindowsGraphicsCaptureD3d11DirectComposition,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum NativeGpuVisualState {
    #[default]
    Idle,
    Aware,
    Analyzing,
    Tool,
    Streaming,
    Speaking,
    Approval,
    Ready,
    Error,
    Sleeping,
}

#[derive(Clone, Copy, Debug, Deserialize)]
pub struct NativeGpuStartRequest {
    pub target_frame_rate: u16,
    #[serde(default)]
    pub capsule_visible: bool,
    #[serde(default)]
    pub visual_state: NativeGpuVisualState,
    #[serde(default = "default_opacity")]
    pub opacity: f32,
}

impl Default for NativeGpuStartRequest {
    fn default() -> Self {
        Self {
            target_frame_rate: 60,
            capsule_visible: true,
            visual_state: NativeGpuVisualState::Idle,
            opacity: default_opacity(),
        }
    }
}

const fn default_opacity() -> f32 {
    0.92
}

#[derive(Clone, Copy, Debug)]
pub struct NativeGpuConfig {
    pub render_hwnd: isize,
    pub input_hwnd: isize,
    pub render_frame: PhysicalFrame,
    pub target_frame_rate: u16,
    pub capsule_visible: bool,
    pub visual_state: NativeGpuVisualState,
    pub opacity: f32,
}

impl NativeGpuConfig {
    pub fn validate(self) -> Result<Self, NativeGpuError> {
        if self.render_hwnd == 0 || self.input_hwnd == 0 {
            return Err(NativeGpuError::InvalidWindow);
        }
        if self.render_frame.width == 0 || self.render_frame.height == 0 {
            return Err(NativeGpuError::InvalidSurface);
        }
        if !matches!(self.target_frame_rate, 60 | 144) {
            return Err(NativeGpuError::InvalidFrameRate);
        }
        if !self.opacity.is_finite() || !(0.2..=1.0).contains(&self.opacity) {
            return Err(NativeGpuError::InvalidOpacity);
        }
        Ok(self)
    }
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct NativeGpuStatus {
    pub backend: NativeGpuBackend,
    pub lifecycle: NativeGpuLifecycle,
    pub zero_copy_capture: bool,
    pub pixel_ipc: bool,
    pub target_frame_rate: u16,
    pub frames_presented: u64,
    pub capture_fps_avg: f64,
    pub frame_interval_p1_fps: f64,
    pub callback_to_present_p95_ms: f64,
    pub present_p95_ms: f64,
    pub surface_width: u32,
    pub surface_height: u32,
    pub monitor_width: u32,
    pub monitor_height: u32,
    pub started_at_ms: Option<u64>,
    pub last_presented_at_ms: Option<u64>,
    pub error_code: Option<String>,
}

impl NativeGpuStatus {
    fn unavailable() -> Self {
        Self {
            backend: NativeGpuBackend::Unavailable,
            lifecycle: NativeGpuLifecycle::Idle,
            zero_copy_capture: false,
            pixel_ipc: false,
            ..Self::default()
        }
    }

    #[cfg(target_os = "windows")]
    fn starting(config: NativeGpuConfig) -> Self {
        Self {
            backend: NativeGpuBackend::WindowsGraphicsCaptureD3d11DirectComposition,
            lifecycle: NativeGpuLifecycle::Starting,
            zero_copy_capture: true,
            pixel_ipc: false,
            target_frame_rate: config.target_frame_rate,
            surface_width: config.render_frame.width,
            surface_height: config.render_frame.height,
            ..Self::default()
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum NativeGpuError {
    #[error("PRESENCE_NATIVE_GPU_INVALID_WINDOW")]
    InvalidWindow,
    #[error("PRESENCE_NATIVE_GPU_INVALID_SURFACE")]
    InvalidSurface,
    #[error("PRESENCE_NATIVE_GPU_INVALID_FRAME_RATE")]
    InvalidFrameRate,
    #[error("PRESENCE_NATIVE_GPU_INVALID_OPACITY")]
    InvalidOpacity,
    #[error("PRESENCE_NATIVE_GPU_ALREADY_RUNNING")]
    AlreadyRunning,
    #[error("PRESENCE_NATIVE_GPU_NOT_RUNNING")]
    NotRunning,
    #[error("PRESENCE_NATIVE_GPU_UNAVAILABLE")]
    Unavailable,
    #[error("PRESENCE_NATIVE_GPU_START_FAILED: {0}")]
    StartFailed(String),
    #[error("PRESENCE_NATIVE_GPU_STOP_FAILED: {0}")]
    StopFailed(String),
}

pub struct NativePresenceGpuManager {
    status: Arc<Mutex<NativeGpuStatus>>,
    #[cfg(target_os = "windows")]
    session: Mutex<Option<windows_backend::WindowsNativeGpuSession>>,
}

impl Default for NativePresenceGpuManager {
    fn default() -> Self {
        Self {
            status: Arc::new(Mutex::new(NativeGpuStatus::unavailable())),
            #[cfg(target_os = "windows")]
            session: Mutex::new(None),
        }
    }
}

impl NativePresenceGpuManager {
    pub fn status(&self) -> NativeGpuStatus {
        self.status
            .lock()
            .map(|status| status.clone())
            .unwrap_or_else(|_| NativeGpuStatus {
                lifecycle: NativeGpuLifecycle::Failed,
                error_code: Some("PRESENCE_NATIVE_GPU_STATUS_LOCK_FAILED".to_owned()),
                ..NativeGpuStatus::unavailable()
            })
    }

    #[cfg(target_os = "windows")]
    pub fn start(&self, config: NativeGpuConfig) -> Result<NativeGpuStatus, NativeGpuError> {
        let config = config.validate()?;
        let mut session = self
            .session
            .lock()
            .map_err(|_| NativeGpuError::StartFailed("session lock unavailable".to_owned()))?;
        if session.is_some() {
            return Err(NativeGpuError::AlreadyRunning);
        }
        self.replace_status(NativeGpuStatus::starting(config));
        match windows_backend::WindowsNativeGpuSession::start(config, Arc::clone(&self.status)) {
            Ok(started) => {
                *session = Some(started);
                Ok(self.status())
            }
            Err(error) => {
                self.replace_status(NativeGpuStatus {
                    backend: NativeGpuBackend::WindowsGraphicsCaptureD3d11DirectComposition,
                    lifecycle: NativeGpuLifecycle::Failed,
                    zero_copy_capture: true,
                    pixel_ipc: false,
                    target_frame_rate: config.target_frame_rate,
                    surface_width: config.render_frame.width,
                    surface_height: config.render_frame.height,
                    error_code: Some(error.to_string()),
                    ..NativeGpuStatus::default()
                });
                Err(error)
            }
        }
    }

    #[cfg(not(target_os = "windows"))]
    pub fn start(&self, config: NativeGpuConfig) -> Result<NativeGpuStatus, NativeGpuError> {
        config.validate()?;
        Err(NativeGpuError::Unavailable)
    }

    #[cfg(target_os = "windows")]
    pub fn stop(&self) -> Result<NativeGpuStatus, NativeGpuError> {
        let mut session = self
            .session
            .lock()
            .map_err(|_| NativeGpuError::StopFailed("session lock unavailable".to_owned()))?;
        let Some(active) = session.take() else {
            self.replace_status(NativeGpuStatus::unavailable());
            return Ok(self.status());
        };
        if let Ok(mut status) = self.status.lock() {
            status.lifecycle = NativeGpuLifecycle::Stopping;
        }
        active.stop()?;
        self.replace_status(NativeGpuStatus::unavailable());
        Ok(self.status())
    }

    #[cfg(target_os = "windows")]
    pub fn prepare_visual_test(&self) -> Result<NativeGpuStatus, NativeGpuError> {
        let session = self
            .session
            .lock()
            .map_err(|_| NativeGpuError::StopFailed("session lock unavailable".to_owned()))?;
        session
            .as_ref()
            .ok_or(NativeGpuError::NotRunning)?
            .prepare_visual_test()?;
        Ok(self.status())
    }

    #[cfg(not(target_os = "windows"))]
    pub fn prepare_visual_test(&self) -> Result<NativeGpuStatus, NativeGpuError> {
        Err(NativeGpuError::Unavailable)
    }

    #[cfg(not(target_os = "windows"))]
    pub fn stop(&self) -> Result<NativeGpuStatus, NativeGpuError> {
        self.replace_status(NativeGpuStatus::unavailable());
        Ok(self.status())
    }

    fn replace_status(&self, next: NativeGpuStatus) {
        if let Ok(mut status) = self.status.lock() {
            *status = next;
        }
    }
}

impl Drop for NativePresenceGpuManager {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_config_accepts_only_supported_deterministic_frame_rates() {
        let base = NativeGpuConfig {
            render_hwnd: 1,
            input_hwnd: 2,
            render_frame: PhysicalFrame {
                x: 0,
                y: 0,
                width: 640,
                height: 260,
            },
            target_frame_rate: 60,
            capsule_visible: true,
            visual_state: NativeGpuVisualState::Idle,
            opacity: 0.9,
        };
        assert!(base.validate().is_ok());
        assert!(NativeGpuConfig {
            target_frame_rate: 144,
            ..base
        }
        .validate()
        .is_ok());
        assert!(matches!(
            NativeGpuConfig {
                target_frame_rate: 120,
                ..base
            }
            .validate(),
            Err(NativeGpuError::InvalidFrameRate)
        ));
    }

    #[test]
    fn native_shader_contract_forbids_cpu_readback_and_pixel_ipc() {
        let source = include_str!("presence_native_gpu/liquid_glass.hlsl");
        let backend = include_str!("presence_native_gpu/windows_backend.rs");
        for forbidden in [
            "D3D11_USAGE_STAGING",
            "D3D11_MAP_READ",
            "frame.buffer(",
            "CopyResource(",
            "ReadPixels",
            "postMessage",
        ] {
            assert!(!source.contains(forbidden));
            assert!(!backend.contains(forbidden));
        }
        for required in [
            "Texture2D<float4> desktop_texture",
            "scene_sdf",
            "thickness_field",
            "fresnel",
            "dispersion_uv",
            "outer_caustic",
            "highlight_primary",
            "highlight_secondary",
            "color) * alpha, alpha",
            "SV_Target",
        ] {
            assert!(
                source.contains(required),
                "missing native GPU contract token: {required}"
            );
        }
    }
}
