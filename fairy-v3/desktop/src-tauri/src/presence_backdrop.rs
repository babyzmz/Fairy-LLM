use std::time::{Instant, SystemTime, UNIX_EPOCH};

use tauri::ipc::Response;
use tauri::{State, WebviewWindow};

use crate::authorize_pet_render_window;
use crate::desktop_preferences::{DesktopPreferencesStore, PetOpticsMode};
use crate::presence_coordinator::PhysicalFrame;
use crate::DesktopState;

const BACKDROP_MAGIC: &[u8; 4] = b"FBG2";
const BACKDROP_HEADER_BYTES: usize = 64;
const MAX_BACKDROP_WIDTH: u32 = 1_024;
const MAX_BACKDROP_HEIGHT: u32 = 512;

#[derive(Debug)]
struct CapturedBackdrop {
    width: u32,
    height: u32,
    source_width: u32,
    source_height: u32,
    sequence: u64,
    captured_at_ms: u64,
    capture_total_us: u64,
    kind: u32,
    rgba: Vec<u8>,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum BackdropExperimentMode {
    #[default]
    Normal,
    CaptureOnly,
    IpcUploadOnly,
}

#[tauri::command]
pub(crate) async fn pet_backdrop_capture(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    sequence: u64,
    experiment_mode: Option<BackdropExperimentMode>,
) -> Result<Response, String> {
    let label = window.label().to_owned();
    authorize_pet_render_window(&label).map_err(|_| "Window is not authorized".to_owned())?;
    let surface_frame = window_frame(&window)?;
    let capture_frame = surface_frame;
    validate_capture_frame(capture_frame)?;
    let experiment_mode = experiment_mode.unwrap_or_default();
    if !cfg!(debug_assertions) && experiment_mode != BackdropExperimentMode::Normal {
        return Err("PRESENCE_BACKDROP_EXPERIMENT_UNAVAILABLE".to_owned());
    }
    let optics_mode = DesktopPreferencesStore::new(&state.data_dir)
        .load()
        .map_err(|_| "PRESENCE_BACKDROP_PREFERENCES_UNAVAILABLE".to_owned())?
        .pet_optics_mode;
    if !backdrop_capture_allowed(optics_mode, experiment_mode, cfg!(debug_assertions)) {
        return Err("PRESENCE_BACKDROP_PRIVACY_MODE".to_owned());
    }
    let captured = tauri::async_runtime::spawn_blocking(move || {
        capture_backdrop(capture_frame, sequence, experiment_mode)
    })
    .await
    .map_err(|_| "PRESENCE_BACKDROP_WORKER_INTERRUPTED".to_owned())??;
    if window_frame(&window)? != surface_frame {
        return Err("PRESENCE_BACKDROP_WINDOW_MOVED".to_owned());
    }
    Ok(Response::new(encode_backdrop(captured)?))
}

fn backdrop_capture_allowed(
    optics_mode: PetOpticsMode,
    experiment_mode: BackdropExperimentMode,
    development: bool,
) -> bool {
    optics_mode == PetOpticsMode::Enhanced
        || (development && experiment_mode != BackdropExperimentMode::Normal)
}

fn window_frame(window: &WebviewWindow) -> Result<PhysicalFrame, String> {
    let position = window.outer_position().map_err(|error| error.to_string())?;
    let size = window.outer_size().map_err(|error| error.to_string())?;
    Ok(PhysicalFrame {
        x: position.x,
        y: position.y,
        width: size.width,
        height: size.height,
    })
}

fn validate_capture_frame(frame: PhysicalFrame) -> Result<(), String> {
    if frame.width == 0
        || frame.height == 0
        || frame.width > MAX_BACKDROP_WIDTH
        || frame.height > MAX_BACKDROP_HEIGHT
    {
        return Err("PRESENCE_BACKDROP_FRAME_OUT_OF_RANGE".to_owned());
    }
    Ok(())
}

#[cfg(target_os = "windows")]
fn capture_backdrop(
    frame: PhysicalFrame,
    sequence: u64,
    experiment_mode: BackdropExperimentMode,
) -> Result<CapturedBackdrop, String> {
    use xcap::Monitor;

    if experiment_mode == BackdropExperimentMode::IpcUploadOnly {
        return synthetic_backdrop(frame, sequence);
    }

    let capture_started = Instant::now();
    let center_x = frame
        .x
        .saturating_add(i32::try_from(frame.width / 2).unwrap_or(i32::MAX));
    let center_y = frame
        .y
        .saturating_add(i32::try_from(frame.height / 2).unwrap_or(i32::MAX));
    let monitor = Monitor::from_point(center_x, center_y)
        .map_err(|_| "PRESENCE_BACKDROP_MONITOR_UNAVAILABLE".to_owned())?;
    let monitor_x = monitor
        .x()
        .map_err(|_| "PRESENCE_BACKDROP_MONITOR_UNAVAILABLE".to_owned())?;
    let monitor_y = monitor
        .y()
        .map_err(|_| "PRESENCE_BACKDROP_MONITOR_UNAVAILABLE".to_owned())?;
    let monitor_width = monitor
        .width()
        .map_err(|_| "PRESENCE_BACKDROP_MONITOR_UNAVAILABLE".to_owned())?;
    let monitor_height = monitor
        .height()
        .map_err(|_| "PRESENCE_BACKDROP_MONITOR_UNAVAILABLE".to_owned())?;
    let relative_x = frame
        .x
        .checked_sub(monitor_x)
        .and_then(|value| u32::try_from(value).ok())
        .ok_or_else(|| "PRESENCE_BACKDROP_FRAME_OUT_OF_MONITOR".to_owned())?;
    let relative_y = frame
        .y
        .checked_sub(monitor_y)
        .and_then(|value| u32::try_from(value).ok())
        .ok_or_else(|| "PRESENCE_BACKDROP_FRAME_OUT_OF_MONITOR".to_owned())?;
    if relative_x.saturating_add(frame.width) > monitor_width
        || relative_y.saturating_add(frame.height) > monitor_height
    {
        return Err("PRESENCE_BACKDROP_FRAME_OUT_OF_MONITOR".to_owned());
    }
    let captured_at_ms = now_ms()?;
    let image = monitor
        .capture_region(relative_x, relative_y, frame.width, frame.height)
        .map_err(|_| "PRESENCE_BACKDROP_CAPTURE_FAILED".to_owned())?;
    let mut captured = CapturedBackdrop {
        width: image.width(),
        height: image.height(),
        source_width: image.width(),
        source_height: image.height(),
        sequence,
        captured_at_ms,
        capture_total_us: elapsed_microseconds(capture_started),
        kind: 0,
        rgba: image.into_raw(),
    };
    if experiment_mode == BackdropExperimentMode::CaptureOnly {
        captured.width = 1;
        captured.height = 1;
        captured.kind = 1;
        captured.rgba = vec![0, 0, 0, 0];
    }
    Ok(captured)
}

#[cfg(not(target_os = "windows"))]
fn capture_backdrop(
    _frame: PhysicalFrame,
    _sequence: u64,
    _experiment_mode: BackdropExperimentMode,
) -> Result<CapturedBackdrop, String> {
    Err("PRESENCE_BACKDROP_UNAVAILABLE".to_owned())
}

fn synthetic_backdrop(frame: PhysicalFrame, sequence: u64) -> Result<CapturedBackdrop, String> {
    let pixel_count = usize::try_from(frame.width)
        .ok()
        .and_then(|width| {
            usize::try_from(frame.height)
                .ok()
                .and_then(|height| width.checked_mul(height))
        })
        .ok_or_else(|| "PRESENCE_BACKDROP_FRAME_OUT_OF_RANGE".to_owned())?;
    let mut rgba = vec![0_u8; pixel_count.saturating_mul(4)];
    for (index, pixel) in rgba.chunks_exact_mut(4).enumerate() {
        let value = ((index * 31) % 251) as u8;
        pixel.copy_from_slice(&[value, value.wrapping_add(17), value.wrapping_add(41), 255]);
    }
    Ok(CapturedBackdrop {
        width: frame.width,
        height: frame.height,
        source_width: frame.width,
        source_height: frame.height,
        sequence,
        captured_at_ms: now_ms()?,
        capture_total_us: 0,
        kind: 2,
        rgba,
    })
}

fn elapsed_microseconds(started: Instant) -> u64 {
    started.elapsed().as_micros().try_into().unwrap_or(u64::MAX)
}

fn now_ms() -> Result<u64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "PRESENCE_BACKDROP_CLOCK_UNAVAILABLE".to_owned())?
        .as_millis()
        .try_into()
        .map_err(|_| "PRESENCE_BACKDROP_CLOCK_UNAVAILABLE".to_owned())
}

fn encode_backdrop(frame: CapturedBackdrop) -> Result<Vec<u8>, String> {
    let pack_started = Instant::now();
    let expected = usize::try_from(frame.width)
        .ok()
        .and_then(|width| {
            usize::try_from(frame.height)
                .ok()
                .and_then(|height| width.checked_mul(height))
        })
        .and_then(|pixels| pixels.checked_mul(4))
        .ok_or_else(|| "PRESENCE_BACKDROP_FRAME_OUT_OF_RANGE".to_owned())?;
    if frame.rgba.len() != expected {
        return Err("PRESENCE_BACKDROP_FRAME_LENGTH_MISMATCH".to_owned());
    }
    let stride = frame
        .width
        .checked_mul(4)
        .ok_or_else(|| "PRESENCE_BACKDROP_FRAME_OUT_OF_RANGE".to_owned())?;
    let mut packet = Vec::with_capacity(BACKDROP_HEADER_BYTES + frame.rgba.len());
    packet.extend_from_slice(BACKDROP_MAGIC);
    packet.extend_from_slice(&frame.width.to_le_bytes());
    packet.extend_from_slice(&frame.height.to_le_bytes());
    packet.extend_from_slice(&stride.to_le_bytes());
    packet.extend_from_slice(&frame.sequence.to_le_bytes());
    packet.extend_from_slice(&frame.captured_at_ms.to_le_bytes());
    packet.extend_from_slice(&frame.capture_total_us.to_le_bytes());
    packet.extend_from_slice(&0_u64.to_le_bytes());
    packet.extend_from_slice(&frame.kind.to_le_bytes());
    packet.extend_from_slice(&(BACKDROP_HEADER_BYTES as u32).to_le_bytes());
    packet.extend_from_slice(&frame.source_width.to_le_bytes());
    packet.extend_from_slice(&frame.source_height.to_le_bytes());
    packet.extend_from_slice(&frame.rgba);
    let pack_us = elapsed_microseconds(pack_started);
    packet[40..48].copy_from_slice(&pack_us.to_le_bytes());
    Ok(packet)
}

#[cfg(target_os = "windows")]
pub fn exclude_window_from_capture(hwnd: isize) -> Result<(), String> {
    set_window_capture_excluded(hwnd, true)
}

#[cfg(target_os = "windows")]
pub fn set_window_capture_excluded(hwnd: isize, excluded: bool) -> Result<(), String> {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        SetWindowDisplayAffinity, WDA_EXCLUDEFROMCAPTURE, WDA_NONE,
    };

    let affinity = if excluded {
        WDA_EXCLUDEFROMCAPTURE
    } else {
        WDA_NONE
    };
    if unsafe { SetWindowDisplayAffinity(hwnd as _, affinity) } == 0 {
        return Err("PRESENCE_BACKDROP_EXCLUSION_FAILED".to_owned());
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
pub fn exclude_window_from_capture(_hwnd: isize) -> Result<(), String> {
    Ok(())
}

#[cfg(not(target_os = "windows"))]
pub fn set_window_capture_excluded(_hwnd: isize, _excluded: bool) -> Result<(), String> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backdrop_packet_has_a_fixed_binary_header_and_exact_rgba_payload() {
        let packet = encode_backdrop(CapturedBackdrop {
            width: 2,
            height: 1,
            source_width: 2,
            source_height: 1,
            sequence: 7,
            captured_at_ms: 11,
            capture_total_us: 3_000,
            kind: 0,
            rgba: vec![1, 2, 3, 4, 5, 6, 7, 8],
        })
        .expect("valid packet");
        assert_eq!(&packet[..4], BACKDROP_MAGIC);
        assert_eq!(u32::from_le_bytes(packet[4..8].try_into().unwrap()), 2);
        assert_eq!(u32::from_le_bytes(packet[8..12].try_into().unwrap()), 1);
        assert_eq!(u32::from_le_bytes(packet[12..16].try_into().unwrap()), 8);
        assert_eq!(u64::from_le_bytes(packet[16..24].try_into().unwrap()), 7);
        assert_eq!(u64::from_le_bytes(packet[24..32].try_into().unwrap()), 11);
        assert_eq!(
            u64::from_le_bytes(packet[32..40].try_into().unwrap()),
            3_000
        );
        let _pack_duration = u64::from_le_bytes(packet[40..48].try_into().unwrap());
        assert_eq!(u32::from_le_bytes(packet[52..56].try_into().unwrap()), 64);
        assert_eq!(&packet[BACKDROP_HEADER_BYTES..], &[1, 2, 3, 4, 5, 6, 7, 8]);
    }

    #[test]
    fn ipc_upload_experiment_generates_a_bounded_deterministic_payload() {
        let frame = PhysicalFrame {
            x: 0,
            y: 0,
            width: 2,
            height: 2,
        };
        let first = synthetic_backdrop(frame, 1).expect("synthetic frame");
        let second = synthetic_backdrop(frame, 2).expect("synthetic frame");
        assert_eq!(first.kind, 2);
        assert_eq!(first.rgba, second.rgba);
        assert_eq!(first.rgba.len(), 16);
        assert_eq!(first.capture_total_us, 0);
    }

    #[test]
    fn standard_privacy_rejects_normal_capture_even_in_development() {
        assert!(!backdrop_capture_allowed(
            PetOpticsMode::Standard,
            BackdropExperimentMode::Normal,
            true,
        ));
        assert!(backdrop_capture_allowed(
            PetOpticsMode::Enhanced,
            BackdropExperimentMode::Normal,
            false,
        ));
        assert!(backdrop_capture_allowed(
            PetOpticsMode::Standard,
            BackdropExperimentMode::CaptureOnly,
            true,
        ));
    }
}
