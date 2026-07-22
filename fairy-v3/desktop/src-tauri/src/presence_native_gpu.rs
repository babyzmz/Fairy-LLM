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
    WindowsHostBackdropD3d11Composition,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum NativeGpuOpticsSource {
    #[default]
    None,
    HostBackdrop,
    HostBackdropPlusMonitorEdge,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum NativeGpuVisualState {
    #[default]
    Idle,
    Aware,
    Forming,
    Input,
    Options,
    Submitting,
    Thinking,
    Tool,
    Responding,
    Speaking,
    Notify,
    Approval,
    Error,
    Returning,
    Suspended,
    Repositioning,
    Sleeping,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum NativeGpuExpansionDirection {
    Left,
    #[default]
    Right,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
pub struct NativeGpuPresentation {
    #[serde(default)]
    pub capsule_visible: bool,
    #[serde(default)]
    pub input_surface_visible: bool,
    #[serde(default = "default_input_surface_height")]
    pub input_surface_height: f32,
    #[serde(default)]
    pub expansion_direction: NativeGpuExpansionDirection,
    #[serde(default)]
    pub visual_state: NativeGpuVisualState,
    #[serde(default = "default_opacity")]
    pub opacity: f32,
    #[serde(default)]
    pub voice_level: f32,
    #[serde(default)]
    pub reduced_motion: bool,
    #[serde(default)]
    pub reduced_transparency: bool,
    #[serde(default)]
    pub increased_contrast: bool,
    #[serde(default = "default_particles_enabled")]
    pub particles_enabled: bool,
    #[serde(default = "default_frame_rate_limit")]
    pub frame_rate_limit: u16,
    #[serde(default)]
    pub shape_droplet: f32,
    #[serde(default)]
    pub shape_bridge: f32,
    #[serde(default)]
    pub shape_capsule: f32,
    #[serde(default)]
    pub returning: bool,
    #[serde(default = "default_core_x")]
    pub core_x: f32,
    #[serde(default = "default_core_y")]
    pub core_y: f32,
    #[serde(default = "default_capsule_x")]
    pub capsule_x: f32,
    #[serde(default = "default_capsule_y")]
    pub capsule_y: f32,
    #[serde(default = "default_capsule_half_width")]
    pub capsule_half_width: f32,
}

impl Default for NativeGpuPresentation {
    fn default() -> Self {
        Self {
            capsule_visible: false,
            input_surface_visible: false,
            input_surface_height: default_input_surface_height(),
            expansion_direction: NativeGpuExpansionDirection::Right,
            visual_state: NativeGpuVisualState::Idle,
            opacity: default_opacity(),
            voice_level: 0.0,
            reduced_motion: false,
            reduced_transparency: false,
            increased_contrast: false,
            particles_enabled: true,
            frame_rate_limit: default_frame_rate_limit(),
            shape_droplet: 0.0,
            shape_bridge: 0.0,
            shape_capsule: 0.0,
            returning: false,
            core_x: default_core_x(),
            core_y: default_core_y(),
            capsule_x: default_capsule_x(),
            capsule_y: default_capsule_y(),
            capsule_half_width: default_capsule_half_width(),
        }
    }
}

impl NativeGpuPresentation {
    pub fn validate(self) -> Result<Self, NativeGpuError> {
        if !self.opacity.is_finite() || !(0.2..=1.0).contains(&self.opacity) {
            return Err(NativeGpuError::InvalidOpacity);
        }
        if !self.voice_level.is_finite() || !(0.0..=1.0).contains(&self.voice_level) {
            return Err(NativeGpuError::InvalidVoiceLevel);
        }
        if !matches!(self.frame_rate_limit, 15 | 30 | 60 | 144 | 300) {
            return Err(NativeGpuError::InvalidFrameRate);
        }
        if [self.shape_droplet, self.shape_bridge, self.shape_capsule]
            .into_iter()
            .any(|value| !value.is_finite() || !(0.0..=1.0).contains(&value))
        {
            return Err(NativeGpuError::InvalidShape);
        }
        if !self.input_surface_height.is_finite()
            || !(64.0..=104.0).contains(&self.input_surface_height)
        {
            return Err(NativeGpuError::InvalidGeometry);
        }
        if !self.core_x.is_finite()
            || !(0.0..=640.0).contains(&self.core_x)
            || !self.core_y.is_finite()
            || !(0.0..=260.0).contains(&self.core_y)
            || !self.capsule_x.is_finite()
            || !(0.0..=640.0).contains(&self.capsule_x)
            || !self.capsule_y.is_finite()
            || !(0.0..=260.0).contains(&self.capsule_y)
            || !self.capsule_half_width.is_finite()
            || !(26.0..=210.0).contains(&self.capsule_half_width)
        {
            return Err(NativeGpuError::InvalidGeometry);
        }
        Ok(self)
    }
}

#[derive(Clone, Copy, Debug, Deserialize)]
pub struct NativeGpuStartRequest {
    pub target_frame_rate: u16,
    #[serde(flatten)]
    pub presentation: NativeGpuPresentation,
}

impl Default for NativeGpuStartRequest {
    fn default() -> Self {
        Self {
            target_frame_rate: 60,
            presentation: NativeGpuPresentation::default(),
        }
    }
}

const fn default_opacity() -> f32 {
    0.92
}

const fn default_input_surface_height() -> f32 {
    64.0
}

const fn default_particles_enabled() -> bool {
    true
}

const fn default_frame_rate_limit() -> u16 {
    300
}

#[derive(Clone, Copy, Debug)]
pub struct NativeGpuConfig {
    pub render_hwnd: isize,
    pub input_hwnd: isize,
    pub render_frame: PhysicalFrame,
    pub always_on_top: bool,
    pub target_frame_rate: u16,
    pub presentation: NativeGpuPresentation,
}

impl NativeGpuConfig {
    pub fn validate(self) -> Result<Self, NativeGpuError> {
        if self.render_hwnd == 0 || self.input_hwnd == 0 {
            return Err(NativeGpuError::InvalidWindow);
        }
        if self.render_frame.width == 0 || self.render_frame.height == 0 {
            return Err(NativeGpuError::InvalidSurface);
        }
        if !matches!(self.target_frame_rate, 60 | 144 | 300) {
            return Err(NativeGpuError::InvalidFrameRate);
        }
        self.presentation.validate()?;
        Ok(self)
    }
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct NativeGpuStatus {
    pub backend: NativeGpuBackend,
    pub optics_source: NativeGpuOpticsSource,
    pub lifecycle: NativeGpuLifecycle,
    pub zero_copy_capture: bool,
    pub pixel_ipc: bool,
    pub hdr_capture: bool,
    pub target_frame_rate: u16,
    pub effective_frame_rate: u16,
    pub display_refresh_rate_hz: u16,
    pub capture_frame_rate_limit: u16,
    pub frames_presented: u64,
    pub capture_fps_avg: f64,
    pub frame_interval_p1_fps: f64,
    pub source_frames_received: u64,
    pub source_capture_fps_avg: f64,
    pub source_frame_interval_p1_fps: f64,
    pub callback_to_present_p95_ms: f64,
    pub present_p95_ms: f64,
    pub surface_width: u32,
    pub surface_height: u32,
    pub target_x: i32,
    pub target_y: i32,
    pub monitor_x: i32,
    pub monitor_y: i32,
    pub monitor_width: u32,
    pub monitor_height: u32,
    pub monitor_handle: Option<String>,
    pub monitor_device_name: Option<String>,
    pub monitor_friendly_name: Option<String>,
    pub adapter_name: Option<String>,
    pub adapter_index: Option<u32>,
    pub output_device_name: Option<String>,
    pub output_index: Option<u32>,
    pub hdr_color_space: Option<String>,
    pub capture_item_width: u32,
    pub capture_item_height: u32,
    pub capture_window_handle: Option<String>,
    pub capture_source_stage: String,
    pub capture_source_hresult: Option<String>,
    pub started_at_ms: Option<u64>,
    pub last_presented_at_ms: Option<u64>,
    pub presentation_revision: u64,
    pub fallback_reason: Option<String>,
    pub error_code: Option<String>,
}

impl NativeGpuStatus {
    fn unavailable() -> Self {
        Self {
            backend: NativeGpuBackend::Unavailable,
            optics_source: NativeGpuOpticsSource::None,
            lifecycle: NativeGpuLifecycle::Idle,
            zero_copy_capture: false,
            pixel_ipc: false,
            capture_source_stage: "idle".to_owned(),
            ..Self::default()
        }
    }

    #[cfg(target_os = "windows")]
    fn starting(config: NativeGpuConfig) -> Self {
        Self {
            backend: NativeGpuBackend::WindowsHostBackdropD3d11Composition,
            optics_source: NativeGpuOpticsSource::HostBackdrop,
            lifecycle: NativeGpuLifecycle::Starting,
            zero_copy_capture: true,
            pixel_ipc: false,
            target_frame_rate: config.target_frame_rate,
            surface_width: config.render_frame.width,
            surface_height: config.render_frame.height,
            target_x: config.render_frame.x,
            target_y: config.render_frame.y,
            capture_source_stage: "selecting_monitor".to_owned(),
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
    #[error("PRESENCE_NATIVE_GPU_INVALID_VOICE_LEVEL")]
    InvalidVoiceLevel,
    #[error("PRESENCE_NATIVE_GPU_INVALID_SHAPE")]
    InvalidShape,
    #[error("PRESENCE_NATIVE_GPU_INVALID_GEOMETRY")]
    InvalidGeometry,
    #[error("PRESENCE_NATIVE_GPU_ALREADY_RUNNING")]
    AlreadyRunning,
    #[error("PRESENCE_NATIVE_GPU_NOT_RUNNING")]
    NotRunning,
    #[error("PRESENCE_NATIVE_GPU_STARTUP_TIMEOUT")]
    StartupTimeout,
    #[error("PRESENCE_NATIVE_GPU_UNAVAILABLE")]
    Unavailable,
    #[error("PRESENCE_NATIVE_GPU_START_FAILED: {0}")]
    StartFailed(String),
    #[error("PRESENCE_NATIVE_GPU_STOP_FAILED: {0}")]
    StopFailed(String),
}

const fn default_core_x() -> f32 {
    96.0
}

const fn default_core_y() -> f32 {
    88.0
}

const fn default_capsule_x() -> f32 {
    164.0
}

const fn default_capsule_y() -> f32 {
    220.0
}

const fn default_capsule_half_width() -> f32 {
    132.0
}

pub struct NativePresenceGpuManager {
    status: Arc<Mutex<NativeGpuStatus>>,
    #[cfg(target_os = "windows")]
    session: Mutex<Option<windows_backend::WindowsNativeGpuSession>>,
    #[cfg(target_os = "windows")]
    operation: Mutex<()>,
}

impl Default for NativePresenceGpuManager {
    fn default() -> Self {
        Self {
            status: Arc::new(Mutex::new(NativeGpuStatus::unavailable())),
            #[cfg(target_os = "windows")]
            session: Mutex::new(None),
            #[cfg(target_os = "windows")]
            operation: Mutex::new(()),
        }
    }
}

impl NativePresenceGpuManager {
    pub fn status(&self) -> NativeGpuStatus {
        #[cfg(target_os = "windows")]
        if let Ok(session) = self.session.lock() {
            if let Some(active) = session.as_ref() {
                return active.status();
            }
        }
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
    pub fn surface_handle(&self) -> Option<isize> {
        self.session
            .lock()
            .ok()?
            .as_ref()
            .and_then(windows_backend::WindowsNativeGpuSession::surface_handle)
    }

    #[cfg(target_os = "windows")]
    pub fn presentation(&self) -> Option<NativeGpuPresentation> {
        self.session.lock().ok()?.as_ref()?.presentation()
    }

    #[cfg(not(target_os = "windows"))]
    pub fn surface_handle(&self) -> Option<isize> {
        None
    }

    #[cfg(not(target_os = "windows"))]
    pub fn presentation(&self) -> Option<NativeGpuPresentation> {
        None
    }

    #[cfg(target_os = "windows")]
    pub fn start(&self, config: NativeGpuConfig) -> Result<NativeGpuStatus, NativeGpuError> {
        let config = config.validate()?;
        let _operation = self
            .operation
            .lock()
            .map_err(|_| NativeGpuError::StartFailed("operation lock unavailable".to_owned()))?;
        let mut session = self
            .session
            .lock()
            .map_err(|_| NativeGpuError::StartFailed("session lock unavailable".to_owned()))?;
        if session.is_some() {
            return Err(NativeGpuError::AlreadyRunning);
        }
        self.replace_status(NativeGpuStatus::starting(config));
        match windows_backend::WindowsNativeGpuSession::start(config, Arc::clone(&self.status)) {
            Ok(started) => match started.wait_for_first_present() {
                Ok(status) => {
                    *session = Some(started);
                    Ok(status)
                }
                Err(error) => {
                    let mut failed = started.status();
                    let _ = started.stop();
                    failed.lifecycle = NativeGpuLifecycle::Failed;
                    failed.error_code = Some(error.to_string());
                    self.replace_status(failed);
                    Err(error)
                }
            },
            Err(error) => {
                if let Ok(mut status) = self.status.lock() {
                    status.lifecycle = NativeGpuLifecycle::Failed;
                    status.error_code = Some(error.to_string());
                }
                Err(error)
            }
        }
    }

    #[cfg(target_os = "windows")]
    pub fn rebind(&self, config: NativeGpuConfig) -> Result<NativeGpuStatus, NativeGpuError> {
        let config = config.validate()?;
        let _operation = self
            .operation
            .lock()
            .map_err(|_| NativeGpuError::StartFailed("operation lock unavailable".to_owned()))?;
        let mut session = self
            .session
            .lock()
            .map_err(|_| NativeGpuError::StartFailed("session lock unavailable".to_owned()))?;
        if session.is_none() {
            return Err(NativeGpuError::NotRunning);
        }
        let drag_active = session
            .as_ref()
            .is_some_and(windows_backend::WindowsNativeGpuSession::is_drag_active);

        let candidate_status = Arc::new(Mutex::new(NativeGpuStatus::starting(config)));
        let candidate =
            windows_backend::WindowsNativeGpuSession::start(config, Arc::clone(&candidate_status))?;
        if drag_active {
            if let Err(error) = candidate.set_drag_active(true) {
                let _ = candidate.stop();
                return Err(error);
            }
        }
        let ready = match candidate.wait_for_first_present() {
            Ok(status) => status,
            Err(error) => {
                let _ = candidate.stop();
                return Err(error);
            }
        };

        let previous = session
            .replace(candidate)
            .ok_or(NativeGpuError::NotRunning)?;
        drop(session);
        self.replace_status(ready.clone());
        if let Err(error) = previous.stop() {
            eprintln!("[presence-native-gpu] replaced source cleanup failed: {error}");
        }
        Ok(ready)
    }

    #[cfg(not(target_os = "windows"))]
    pub fn rebind(&self, config: NativeGpuConfig) -> Result<NativeGpuStatus, NativeGpuError> {
        config.validate()?;
        Err(NativeGpuError::Unavailable)
    }

    #[cfg(not(target_os = "windows"))]
    pub fn start(&self, config: NativeGpuConfig) -> Result<NativeGpuStatus, NativeGpuError> {
        config.validate()?;
        Err(NativeGpuError::Unavailable)
    }

    #[cfg(target_os = "windows")]
    pub fn stop(&self) -> Result<NativeGpuStatus, NativeGpuError> {
        let _operation = self
            .operation
            .lock()
            .map_err(|_| NativeGpuError::StopFailed("operation lock unavailable".to_owned()))?;
        let mut session = self
            .session
            .lock()
            .map_err(|_| NativeGpuError::StopFailed("session lock unavailable".to_owned()))?;
        let Some(active) = session.take() else {
            self.replace_status(NativeGpuStatus::unavailable());
            drop(session);
            return Ok(self.status());
        };
        if let Ok(mut status) = self.status.lock() {
            status.lifecycle = NativeGpuLifecycle::Stopping;
        }
        drop(session);
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
        let active = session.as_ref().ok_or(NativeGpuError::NotRunning)?;
        active.prepare_visual_test()?;
        Ok(active.status())
    }

    #[cfg(target_os = "windows")]
    pub fn update(
        &self,
        presentation: NativeGpuPresentation,
    ) -> Result<NativeGpuStatus, NativeGpuError> {
        let presentation = presentation.validate()?;
        let session = self
            .session
            .lock()
            .map_err(|_| NativeGpuError::StartFailed("session lock unavailable".to_owned()))?;
        let active = session.as_ref().ok_or(NativeGpuError::NotRunning)?;
        active.update(presentation)?;
        Ok(active.status())
    }

    #[cfg(target_os = "windows")]
    pub fn set_always_on_top(&self, always_on_top: bool) -> Result<(), NativeGpuError> {
        let session = self
            .session
            .lock()
            .map_err(|_| NativeGpuError::StartFailed("session lock unavailable".to_owned()))?;
        if let Some(session) = session.as_ref() {
            session.set_always_on_top(always_on_top)?;
        }
        Ok(())
    }

    #[cfg(target_os = "windows")]
    pub fn set_drag_active(&self, active: bool) -> Result<(), NativeGpuError> {
        let session = self
            .session
            .lock()
            .map_err(|_| NativeGpuError::StartFailed("session lock unavailable".to_owned()))?;
        if let Some(session) = session.as_ref() {
            session.set_drag_active(active)?;
        }
        Ok(())
    }

    #[cfg(not(target_os = "windows"))]
    pub fn prepare_visual_test(&self) -> Result<NativeGpuStatus, NativeGpuError> {
        Err(NativeGpuError::Unavailable)
    }

    #[cfg(not(target_os = "windows"))]
    pub fn update(
        &self,
        presentation: NativeGpuPresentation,
    ) -> Result<NativeGpuStatus, NativeGpuError> {
        presentation.validate()?;
        Err(NativeGpuError::Unavailable)
    }

    #[cfg(not(target_os = "windows"))]
    pub fn set_always_on_top(&self, _always_on_top: bool) -> Result<(), NativeGpuError> {
        Ok(())
    }

    #[cfg(not(target_os = "windows"))]
    pub fn set_drag_active(&self, _active: bool) -> Result<(), NativeGpuError> {
        Ok(())
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
    fn native_start_request_defaults_to_core_only() {
        let request = NativeGpuStartRequest::default();
        assert!(!request.presentation.capsule_visible);
        assert_eq!(request.presentation.shape_droplet, 0.0);
        assert_eq!(request.presentation.shape_bridge, 0.0);
        assert_eq!(request.presentation.shape_capsule, 0.0);
        assert!(!request.presentation.returning);
    }

    #[test]
    fn native_probe_restores_product_projection_without_leaving_a_capsule() {
        let probe = include_str!("../../scripts/probe-presence-native-gpu.mjs");
        assert!(probe.contains("capsule_visible: false"));
        assert!(probe.contains("shape_capsule: 0"));
        assert!(probe.contains("[\"run\", \"cadence\", \"rebind\"].includes(action)"));
        assert!(probe.contains("await page.reload({ waitUntil: \"domcontentloaded\" })"));
        assert!(!probe.contains("capsule_visible: true"));
    }

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
            always_on_top: true,
            target_frame_rate: 60,
            presentation: NativeGpuPresentation {
                capsule_visible: true,
                opacity: 0.9,
                ..NativeGpuPresentation::default()
            },
        };
        for target_frame_rate in [60, 144, 300] {
            assert!(NativeGpuConfig {
                target_frame_rate,
                ..base
            }
            .validate()
            .is_ok());
        }
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
    fn native_presentation_rejects_unbounded_scalar_state() {
        assert!(matches!(
            NativeGpuPresentation {
                voice_level: 1.1,
                ..NativeGpuPresentation::default()
            }
            .validate(),
            Err(NativeGpuError::InvalidVoiceLevel)
        ));
        assert!(matches!(
            NativeGpuPresentation {
                opacity: f32::NAN,
                ..NativeGpuPresentation::default()
            }
            .validate(),
            Err(NativeGpuError::InvalidOpacity)
        ));
        assert!(matches!(
            NativeGpuPresentation {
                shape_bridge: 1.1,
                ..NativeGpuPresentation::default()
            }
            .validate(),
            Err(NativeGpuError::InvalidShape)
        ));
    }

    #[test]
    fn native_shader_contract_forbids_cpu_readback_pixel_ipc_and_recursive_overlay_cleanup() {
        let source = include_str!("presence_native_gpu/liquid_glass.hlsl");
        let backend = include_str!("presence_native_gpu/windows_backend.rs");
        let production = backend.split("#[cfg(test)]").next().unwrap_or(backend);
        for forbidden in [
            "D3D11_USAGE_STAGING",
            "D3D11_MAP_READ",
            "frame.buffer(",
            "ReadPixels",
            "postMessage",
        ] {
            assert!(!source.contains(forbidden));
            assert!(!production.contains(forbidden));
        }
        for forbidden in [
            "core_warp",
            "capsule_warp",
            "captured - overlay.rgb",
            "/ denominator",
            "sample_desktop_linear",
            "previous_overlay",
            "CleanBackdropCache",
            "DwmFlush()",
        ] {
            assert!(!source.contains(forbidden));
            assert!(!production.contains(forbidden));
        }
        for required in [
            "scene_sdf",
            "thickness_field",
            "rim_fresnel",
            "outer_caustic",
            "key_highlight",
            "fill_highlight",
            "shape_bridge",
            "return min(result, capsule)",
            "EDGE_LENS_DEPTH_PX",
            "edge_refraction_profile",
            "sample_refracted_desktop",
            "desktop_texture",
            "inward_depth * 1.65 + outside_clearance",
            "drag_center_shift",
            "state_flow",
            "center_alpha",
            "edge_material",
            "linear_to_srgb",
            "identity_premultiplied",
            "foreground_premultiplied",
            "SV_Target",
        ] {
            assert!(
                source.contains(required),
                "missing native GPU contract token: {required}"
            );
        }
        for required in [
            "DWMWA_USE_HOSTBACKDROPBRUSH",
            "CreateHostBackdropBrush()",
            "CreateCompositionSurfaceForSwapChain(swap_chain)",
            "DuplicateOutput(&dxgi_device)",
            "CreateShaderResourceView(&texture",
            "EDGE_CAPTURE_REBASE_FRAMES",
            ".update_lens(presentation, drag, render_frame)",
            "capture_source_valid: f32::from(edge_capture_ready)",
        ] {
            assert!(
                production.contains(required),
                "missing Host Backdrop contract token: {required}"
            );
        }
        for forbidden in [
            "windows_capture",
            "GraphicsCaptureItem",
            "CreateForMonitor",
            "NativeCaptureHandler",
        ] {
            assert!(
                !production.contains(forbidden),
                "WGC returned to the production Presence path: {forbidden}"
            );
        }
        assert!(!source.contains("remove_previous_overlay"));
        assert!(!source.contains("previous_overlay_texture"));
    }
}
