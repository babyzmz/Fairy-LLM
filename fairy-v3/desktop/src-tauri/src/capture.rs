use std::fmt;
use std::io::Cursor;
use std::time::{SystemTime, UNIX_EPOCH};

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine as _;
use image::{DynamicImage, ImageFormat};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri::WebviewWindow;

pub const MAX_CAPTURE_BYTES: usize = 20 * 1024 * 1024;
pub const MAX_CAPTURE_PIXELS: u64 = 33_177_600;
const MAX_CAPTURE_DIMENSION: u32 = 16_384;
const MAX_CAPTURE_SURFACES: usize = 256;
const PNG_SIGNATURE: &[u8; 8] = b"\x89PNG\r\n\x1a\n";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SurfaceKind {
    Display,
    Window,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CaptureRequest {
    pub kind: SurfaceKind,
    pub source_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CaptureSurface {
    pub kind: SurfaceKind,
    pub source_id: String,
    pub label: String,
    pub width: u32,
    pub height: u32,
    pub is_primary: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CapturedFrame {
    pub width: u32,
    pub height: u32,
    pub png: Vec<u8>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CaptureResult {
    pub kind: SurfaceKind,
    pub source_id: String,
    pub source_label: String,
    pub media_type: &'static str,
    pub png_base64: String,
    pub width: u32,
    pub height: u32,
    pub byte_length: usize,
    pub content_hash: String,
    pub captured_at_ms: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CaptureWindowError;

pub fn authorize_capture_window(label: &str) -> Result<(), CaptureWindowError> {
    if label == "main" {
        Ok(())
    } else {
        Err(CaptureWindowError)
    }
}

pub fn authorize_capture_list_window(label: &str) -> Result<(), CaptureWindowError> {
    if ["main", "companion"].contains(&label) {
        Ok(())
    } else {
        Err(CaptureWindowError)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CaptureError {
    ScopeMismatch,
    SourceNotFound,
    LimitExceeded(&'static str),
    InvalidSource(&'static str),
    Unavailable,
}

impl CaptureError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::ScopeMismatch => "SCOPE_MISMATCH",
            Self::SourceNotFound => "CAPTURE_SOURCE_NOT_FOUND",
            Self::LimitExceeded(_) => "CAPTURE_LIMIT_EXCEEDED",
            Self::InvalidSource(_) => "CAPTURE_SOURCE_INVALID",
            Self::Unavailable => "CAPTURE_UNAVAILABLE",
        }
    }
}

impl fmt::Display for CaptureError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = match self {
            Self::ScopeMismatch => "window is not authorized to capture a surface",
            Self::SourceNotFound => "capture source was not found",
            Self::LimitExceeded(message) | Self::InvalidSource(message) => message,
            Self::Unavailable => "screen capture is unavailable",
        };
        formatter.write_str(message)
    }
}

impl std::error::Error for CaptureError {}

pub trait CaptureBackend {
    fn list_surfaces(&self) -> Result<Vec<CaptureSurface>, CaptureError>;
    fn capture(&self, surface: &CaptureSurface) -> Result<CapturedFrame, CaptureError>;
}

pub struct CaptureService<B> {
    backend: B,
}

impl<B: CaptureBackend> CaptureService<B> {
    pub fn new(backend: B) -> Self {
        Self { backend }
    }

    pub fn list(&self, window_label: &str) -> Result<Vec<CaptureSurface>, CaptureError> {
        authorize_capture_list_window(window_label).map_err(|_| CaptureError::ScopeMismatch)?;
        self.validated_surfaces()
    }

    fn validated_surfaces(&self) -> Result<Vec<CaptureSurface>, CaptureError> {
        let surfaces = self.backend.list_surfaces()?;
        if surfaces.len() > MAX_CAPTURE_SURFACES {
            return Err(CaptureError::LimitExceeded("too many capture surfaces"));
        }
        for surface in &surfaces {
            validate_surface(surface)?;
        }
        Ok(surfaces)
    }

    pub fn capture(
        &self,
        window_label: &str,
        request: CaptureRequest,
    ) -> Result<CaptureResult, CaptureError> {
        authorize_capture_window(window_label).map_err(|_| CaptureError::ScopeMismatch)?;
        validate_source_id(&request.source_id)?;
        let surfaces = self.validated_surfaces()?;
        let surface = surfaces
            .into_iter()
            .find(|surface| surface.kind == request.kind && surface.source_id == request.source_id)
            .ok_or(CaptureError::SourceNotFound)?;
        validate_surface(&surface)?;
        let frame = self.backend.capture(&surface)?;
        validate_dimensions(frame.width, frame.height)?;
        if frame.width != surface.width || frame.height != surface.height {
            return Err(CaptureError::InvalidSource(
                "capture source dimensions changed during capture",
            ));
        }
        if frame.png.len() < PNG_SIGNATURE.len()
            || frame.png.len() > MAX_CAPTURE_BYTES
            || &frame.png[..PNG_SIGNATURE.len()] != PNG_SIGNATURE
        {
            return Err(CaptureError::LimitExceeded(
                "captured PNG exceeds its encoded byte limit",
            ));
        }
        let content_hash = format!("{:x}", Sha256::digest(&frame.png));
        let captured_at_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| CaptureError::Unavailable)?
            .as_millis()
            .try_into()
            .map_err(|_| CaptureError::Unavailable)?;
        Ok(CaptureResult {
            kind: surface.kind,
            source_id: surface.source_id,
            source_label: surface.label,
            media_type: "image/png",
            png_base64: BASE64.encode(&frame.png),
            width: frame.width,
            height: frame.height,
            byte_length: frame.png.len(),
            content_hash,
            captured_at_ms,
        })
    }
}

fn validate_surface(surface: &CaptureSurface) -> Result<(), CaptureError> {
    validate_source_id(&surface.source_id)?;
    validate_dimensions(surface.width, surface.height)?;
    if surface.label.is_empty()
        || surface.label.len() > 255
        || surface.label.chars().any(char::is_control)
    {
        return Err(CaptureError::InvalidSource(
            "capture source label is invalid",
        ));
    }
    Ok(())
}

fn validate_source_id(source_id: &str) -> Result<(), CaptureError> {
    if source_id.is_empty()
        || source_id.len() > 10
        || !source_id.bytes().all(|value| value.is_ascii_digit())
        || source_id.parse::<u32>().is_err()
    {
        return Err(CaptureError::InvalidSource("capture source id is invalid"));
    }
    Ok(())
}

fn validate_dimensions(width: u32, height: u32) -> Result<(), CaptureError> {
    if width == 0
        || height == 0
        || width > MAX_CAPTURE_DIMENSION
        || height > MAX_CAPTURE_DIMENSION
        || u64::from(width) * u64::from(height) > MAX_CAPTURE_PIXELS
    {
        return Err(CaptureError::LimitExceeded(
            "capture dimensions exceed the pixel limit",
        ));
    }
    Ok(())
}

#[derive(Clone, Copy)]
struct SystemCaptureBackend;

#[cfg(target_os = "windows")]
impl CaptureBackend for SystemCaptureBackend {
    fn list_surfaces(&self) -> Result<Vec<CaptureSurface>, CaptureError> {
        use xcap::{Monitor, Window};

        let mut surfaces = Vec::new();
        for monitor in Monitor::all().map_err(|_| CaptureError::Unavailable)? {
            surfaces.push(CaptureSurface {
                kind: SurfaceKind::Display,
                source_id: monitor
                    .id()
                    .map_err(|_| CaptureError::Unavailable)?
                    .to_string(),
                label: monitor
                    .friendly_name()
                    .map_err(|_| CaptureError::Unavailable)?,
                width: monitor.width().map_err(|_| CaptureError::Unavailable)?,
                height: monitor.height().map_err(|_| CaptureError::Unavailable)?,
                is_primary: monitor
                    .is_primary()
                    .map_err(|_| CaptureError::Unavailable)?,
            });
        }
        for window in Window::all().map_err(|_| CaptureError::Unavailable)? {
            if window
                .is_minimized()
                .map_err(|_| CaptureError::Unavailable)?
            {
                continue;
            }
            let label = window.title().map_err(|_| CaptureError::Unavailable)?;
            if label.trim().is_empty() {
                continue;
            }
            surfaces.push(CaptureSurface {
                kind: SurfaceKind::Window,
                source_id: window
                    .id()
                    .map_err(|_| CaptureError::Unavailable)?
                    .to_string(),
                label,
                width: window.width().map_err(|_| CaptureError::Unavailable)?,
                height: window.height().map_err(|_| CaptureError::Unavailable)?,
                is_primary: false,
            });
            if surfaces.len() >= MAX_CAPTURE_SURFACES {
                break;
            }
        }
        Ok(surfaces)
    }

    fn capture(&self, surface: &CaptureSurface) -> Result<CapturedFrame, CaptureError> {
        use xcap::{Monitor, Window};

        let source_id = surface
            .source_id
            .parse::<u32>()
            .map_err(|_| CaptureError::SourceNotFound)?;
        let image = match surface.kind {
            SurfaceKind::Display => Monitor::all()
                .map_err(|_| CaptureError::Unavailable)?
                .into_iter()
                .find(|monitor| monitor.id().ok() == Some(source_id))
                .ok_or(CaptureError::SourceNotFound)?
                .capture_image()
                .map_err(|_| CaptureError::Unavailable)?,
            SurfaceKind::Window => Window::all()
                .map_err(|_| CaptureError::Unavailable)?
                .into_iter()
                .find(|window| window.id().ok() == Some(source_id))
                .ok_or(CaptureError::SourceNotFound)?
                .capture_image()
                .map_err(|_| CaptureError::Unavailable)?,
        };
        validate_dimensions(image.width(), image.height())?;
        let width = image.width();
        let height = image.height();
        let mut output = Cursor::new(Vec::new());
        DynamicImage::ImageRgba8(image)
            .write_to(&mut output, ImageFormat::Png)
            .map_err(|_| CaptureError::Unavailable)?;
        Ok(CapturedFrame {
            width,
            height,
            png: output.into_inner(),
        })
    }
}

#[cfg(not(target_os = "windows"))]
impl CaptureBackend for SystemCaptureBackend {
    fn list_surfaces(&self) -> Result<Vec<CaptureSurface>, CaptureError> {
        Err(CaptureError::Unavailable)
    }

    fn capture(&self, _surface: &CaptureSurface) -> Result<CapturedFrame, CaptureError> {
        Err(CaptureError::Unavailable)
    }
}

#[tauri::command]
pub async fn list_capture_surfaces(window: WebviewWindow) -> Result<Vec<CaptureSurface>, String> {
    let label = window.label().to_owned();
    tauri::async_runtime::spawn_blocking(move || {
        CaptureService::new(SystemCaptureBackend).list(&label)
    })
    .await
    .map_err(|_| "CAPTURE_UNAVAILABLE: screen capture worker interrupted".to_owned())?
    .map_err(public_error)
}

#[tauri::command]
pub async fn capture_surface(
    window: WebviewWindow,
    request: CaptureRequest,
) -> Result<CaptureResult, String> {
    let label = window.label().to_owned();
    tauri::async_runtime::spawn_blocking(move || {
        CaptureService::new(SystemCaptureBackend).capture(&label, request)
    })
    .await
    .map_err(|_| "CAPTURE_UNAVAILABLE: screen capture worker interrupted".to_owned())?
    .map_err(public_error)
}

fn public_error(error: CaptureError) -> String {
    format!("{}: {error}", error.code())
}

#[cfg(test)]
mod tests {
    use super::{authorize_capture_list_window, authorize_capture_window};

    #[test]
    fn companion_can_list_sources_but_cannot_capture_frames() {
        assert!(authorize_capture_list_window("companion").is_ok());
        assert!(authorize_capture_window("companion").is_err());
        assert!(authorize_capture_window("main").is_ok());
    }
}
