use fairy_desktop_v3::capture::{
    authorize_capture_window, CaptureBackend, CaptureError, CaptureRequest, CaptureService,
    CaptureSurface, CapturedFrame, SurfaceKind, MAX_CAPTURE_BYTES, MAX_CAPTURE_PIXELS,
};

#[derive(Clone)]
struct FakeBackend {
    surfaces: Vec<CaptureSurface>,
    frame: CapturedFrame,
}

impl CaptureBackend for FakeBackend {
    fn list_surfaces(&self) -> Result<Vec<CaptureSurface>, CaptureError> {
        Ok(self.surfaces.clone())
    }

    fn capture(&self, _surface: &CaptureSurface) -> Result<CapturedFrame, CaptureError> {
        Ok(self.frame.clone())
    }
}

fn surface(width: u32, height: u32) -> CaptureSurface {
    CaptureSurface {
        kind: SurfaceKind::Display,
        source_id: "7".to_owned(),
        label: "Primary display".to_owned(),
        width,
        height,
        is_primary: true,
    }
}

fn backend(width: u32, height: u32, png: Vec<u8>) -> FakeBackend {
    FakeBackend {
        surfaces: vec![surface(width, height)],
        frame: CapturedFrame { width, height, png },
    }
}

#[test]
fn capture_is_authorized_only_for_the_main_window() {
    assert!(authorize_capture_window("main").is_ok());
    assert!(authorize_capture_window("pet").is_err());
    assert!(authorize_capture_window("guide").is_err());
    assert!(authorize_capture_window("preview").is_err());
}

#[test]
fn invalid_display_or_window_ids_are_rejected_before_capture() {
    let service = CaptureService::new(backend(1, 1, png(32)));
    let error = service
        .capture(
            "main",
            CaptureRequest {
                kind: SurfaceKind::Display,
                source_id: "8".to_owned(),
            },
        )
        .expect_err("unknown source must fail");

    assert_eq!(error.code(), "CAPTURE_SOURCE_NOT_FOUND");
}

#[test]
fn capture_rejects_pixel_and_encoded_byte_limits() {
    let oversized_width = (MAX_CAPTURE_PIXELS + 1) as u32;
    let pixel_error = CaptureService::new(backend(oversized_width, 1, png(32)))
        .capture(
            "main",
            CaptureRequest {
                kind: SurfaceKind::Display,
                source_id: "7".to_owned(),
            },
        )
        .expect_err("oversized dimensions must fail");
    assert_eq!(pixel_error.code(), "CAPTURE_LIMIT_EXCEEDED");

    let byte_error = CaptureService::new(backend(1, 1, png(MAX_CAPTURE_BYTES + 1)))
        .capture(
            "main",
            CaptureRequest {
                kind: SurfaceKind::Display,
                source_id: "7".to_owned(),
            },
        )
        .expect_err("oversized PNG must fail");
    assert_eq!(byte_error.code(), "CAPTURE_LIMIT_EXCEEDED");
}

#[test]
fn capture_module_has_no_input_control_process_or_background_watcher_api() {
    let source = include_str!("../src/capture.rs");
    for forbidden in [
        "std::process",
        "Command::new",
        "SendInput",
        "SetCursorPos",
        "GetCursorPos",
        "keybd_event",
        "mouse_event",
        ".pid()",
        ".app_name()",
        "video_recorder",
    ] {
        assert!(
            !source.contains(forbidden),
            "capture module must not contain {forbidden}"
        );
    }
}

fn png(length: usize) -> Vec<u8> {
    let mut value = vec![0; length.max(8)];
    value[..8].copy_from_slice(&[137, 80, 78, 71, 13, 10, 26, 10]);
    value
}
