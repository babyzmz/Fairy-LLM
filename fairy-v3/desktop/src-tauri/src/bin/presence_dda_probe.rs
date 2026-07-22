#[cfg(not(target_os = "windows"))]
fn main() {
    println!(r#"{"passed":false,"error":"PRESENCE_DDA_PROBE_WINDOWS_ONLY"}"#);
}

#[cfg(target_os = "windows")]
mod windows_probe {
    use std::thread;
    use std::time::{Duration, Instant};

    use fairy_windows_capture_dda::{
        create_device_for_output, set_window_excluded_from_dda, DesktopTexturePoll,
        DesktopTextureSource, DisplayOutputBinding, DuplicationFormat, DuplicationSession,
    };
    use serde::Serialize;
    use serde_json::{json, Value};
    use sha2::{Digest, Sha256};
    use windows::core::{w, Interface, PCWSTR};
    use windows::Win32::Foundation::{
        COLORREF, HINSTANCE, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM,
    };
    use windows::Win32::Graphics::Direct3D11::{
        ID3D11Device, ID3D11DeviceContext, ID3D11Resource, ID3D11Texture2D, D3D11_BOX,
        D3D11_CPU_ACCESS_READ, D3D11_MAPPED_SUBRESOURCE, D3D11_MAP_READ, D3D11_TEXTURE2D_DESC,
        D3D11_USAGE_STAGING,
    };
    use windows::Win32::Graphics::Dwm::DwmFlush;
    use windows::Win32::Graphics::Dxgi::Common::{
        DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_R10G10B10A2_UNORM, DXGI_FORMAT_R16G16B16A16_FLOAT,
        DXGI_MODE_ROTATION_IDENTITY,
    };
    use windows::Win32::Graphics::Gdi::{
        BeginPaint, CreateSolidBrush, DeleteObject, EndPaint, FillRect, InvalidateRect,
        MonitorFromPoint, UpdateWindow, HGDIOBJ, MONITOR_DEFAULTTONEAREST, PAINTSTRUCT,
    };
    use windows::Win32::System::LibraryLoader::GetModuleHandleW;
    use windows::Win32::UI::HiDpi::{
        SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
    };
    use windows::Win32::UI::WindowsAndMessaging::{
        CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW, GetClientRect,
        GetCursorPos, GetWindowLongPtrW, PeekMessageW, RegisterClassW, SetWindowLongPtrW,
        SetWindowPos, ShowWindow, TranslateMessage, CS_HREDRAW, CS_VREDRAW, GWLP_USERDATA,
        HWND_TOPMOST, MSG, PM_REMOVE, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SW_SHOWNOACTIVATE,
        WM_ERASEBKGND, WM_PAINT, WNDCLASSW, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST,
        WS_POPUP,
    };
    use xcap::Monitor;

    const WINDOW_CLASS: PCWSTR = w!("FairyPresenceDdaProbeWindow");
    const TEST_WIDTH: i32 = 144;
    const TEST_HEIGHT: i32 = 112;
    const SAMPLE_INSET: i32 = 16;
    const CYAN: Rgb = Rgb {
        r: 0,
        g: 220,
        b: 255,
    };
    const MAGENTA: Rgb = Rgb {
        r: 255,
        g: 0,
        b: 255,
    };
    const GREEN: Rgb = Rgb {
        r: 24,
        g: 240,
        b: 72,
    };
    const TRIGGER: Rgb = Rgb {
        r: 255,
        g: 220,
        b: 0,
    };

    #[derive(Clone, Copy)]
    struct Rgb {
        r: u8,
        g: u8,
        b: u8,
    }

    impl Rgb {
        const fn colorref(self) -> isize {
            (self.r as isize) | ((self.g as isize) << 8) | ((self.b as isize) << 16)
        }
    }

    #[derive(Clone, Debug, Serialize)]
    struct PixelStats {
        sha256: String,
        cyan_ratio: f64,
        magenta_ratio: f64,
        green_ratio: f64,
        black_ratio: f64,
        mean_rgb: [f64; 3],
    }

    struct ProbeWindow(HWND);

    impl ProbeWindow {
        fn create(rect: RECT, color: Rgb) -> Result<Self, String> {
            register_window_class()?;
            let module = unsafe { GetModuleHandleW(PCWSTR::null()) }
                .map_err(|error| format!("PROBE_MODULE: {error}"))?;
            let hwnd = unsafe {
                CreateWindowExW(
                    WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST,
                    WINDOW_CLASS,
                    PCWSTR::null(),
                    WS_POPUP,
                    rect.left,
                    rect.top,
                    rect.right - rect.left,
                    rect.bottom - rect.top,
                    None,
                    None,
                    Some(HINSTANCE(module.0)),
                    None,
                )
            }
            .map_err(|error| format!("PROBE_WINDOW_CREATE: {error}"))?;
            unsafe { SetWindowLongPtrW(hwnd, GWLP_USERDATA, color.colorref()) };
            Ok(Self(hwnd))
        }

        fn hwnd(&self) -> HWND {
            self.0
        }

        fn show(&self) -> Result<(), String> {
            unsafe {
                SetWindowPos(
                    self.0,
                    Some(HWND_TOPMOST),
                    0,
                    0,
                    0,
                    0,
                    SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE,
                )
                .map_err(|error| format!("PROBE_WINDOW_TOPMOST: {error}"))?;
                let _ = ShowWindow(self.0, SW_SHOWNOACTIVATE);
                if !InvalidateRect(Some(self.0), None, true).as_bool() {
                    return Err(format!(
                        "PROBE_WINDOW_INVALIDATE: {}",
                        windows::core::Error::from_thread()
                    ));
                }
                if !UpdateWindow(self.0).as_bool() {
                    return Err(format!(
                        "PROBE_WINDOW_UPDATE: {}",
                        windows::core::Error::from_thread()
                    ));
                }
                DwmFlush().map_err(|error| format!("PROBE_DWM_FLUSH: {error}"))?;
            }
            pump_messages();
            thread::sleep(Duration::from_millis(80));
            Ok(())
        }

        fn set_color(&self, color: Rgb) -> Result<(), String> {
            unsafe {
                SetWindowLongPtrW(self.0, GWLP_USERDATA, color.colorref());
                if !InvalidateRect(Some(self.0), None, true).as_bool() {
                    return Err(format!(
                        "PROBE_WINDOW_INVALIDATE: {}",
                        windows::core::Error::from_thread()
                    ));
                }
                if !UpdateWindow(self.0).as_bool() {
                    return Err(format!(
                        "PROBE_WINDOW_UPDATE: {}",
                        windows::core::Error::from_thread()
                    ));
                }
                DwmFlush().map_err(|error| format!("PROBE_DWM_FLUSH: {error}"))?;
            }
            pump_messages();
            Ok(())
        }

        fn move_by(&self, delta_x: i32) -> Result<(), String> {
            let mut rect = RECT::default();
            unsafe { windows::Win32::UI::WindowsAndMessaging::GetWindowRect(self.0, &mut rect) }
                .map_err(|error| format!("PROBE_TRIGGER_RECT: {error}"))?;
            unsafe {
                SetWindowPos(
                    self.0,
                    Some(HWND_TOPMOST),
                    rect.left + delta_x,
                    rect.top,
                    0,
                    0,
                    SWP_NOACTIVATE | SWP_NOSIZE,
                )
                .map_err(|error| format!("PROBE_TRIGGER_MOVE: {error}"))?;
                DwmFlush().map_err(|error| format!("PROBE_DWM_FLUSH: {error}"))?;
            }
            pump_messages();
            thread::sleep(Duration::from_millis(40));
            Ok(())
        }
    }

    impl Drop for ProbeWindow {
        fn drop(&mut self) {
            let _ = unsafe { DestroyWindow(self.0) };
            pump_messages();
        }
    }

    pub fn main() {
        let result = run();
        match result {
            Ok(report) => {
                println!("{report}");
                if report.get("passed").and_then(Value::as_bool) != Some(true) {
                    std::process::exit(2);
                }
            }
            Err(error) => {
                println!("{}", json!({ "passed": false, "error": error }));
                std::process::exit(1);
            }
        }
    }

    fn run() -> Result<Value, String> {
        unsafe {
            let _ = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        }
        let mut cursor = POINT::default();
        unsafe { GetCursorPos(&mut cursor) }.map_err(|error| format!("PROBE_CURSOR: {error}"))?;
        let monitor = unsafe { MonitorFromPoint(cursor, MONITOR_DEFAULTTONEAREST) };
        let binding = DisplayOutputBinding::for_monitor(monitor)
            .map_err(|error| format!("PROBE_OUTPUT_BINDING: {error}"))?;
        let desktop = binding.desktop_coordinates();
        let test_rect = centered_rect(desktop, TEST_WIDTH, TEST_HEIGHT);
        let sample_rect = inset_rect(test_rect, SAMPLE_INSET);
        let trigger_rect = RECT {
            left: desktop.left + 12,
            top: desktop.top + 12,
            right: desktop.left + 28,
            bottom: desktop.top + 28,
        };
        let source_roi = output_relative_rect(sample_rect, desktop)?;

        let (device, context) =
            create_device_for_output(&binding).map_err(|error| format!("PROBE_DEVICE: {error}"))?;
        let mut session = DuplicationSession::new_with_device_output(
            device.clone(),
            context.clone(),
            binding.output().clone(),
            &[
                DuplicationFormat::Bgra8,
                DuplicationFormat::Rgb10A2,
                DuplicationFormat::Rgba16F,
            ],
        )
        .map_err(|error| format!("PROBE_DUPLICATION: {error}"))?;
        if session.description().Rotation != DXGI_MODE_ROTATION_IDENTITY {
            return Err(format!(
                "PROBE_ROTATED_OUTPUT_UNSUPPORTED: {}",
                session.description().Rotation.0
            ));
        }

        let background = ProbeWindow::create(test_rect, CYAN)?;
        background.show()?;
        let baseline = wait_for_dda_color(
            &mut session,
            &device,
            &context,
            source_roi,
            CYAN,
            Duration::from_secs(2),
        )?;

        let overlay = ProbeWindow::create(test_rect, MAGENTA)?;
        set_window_excluded_from_dda(overlay.hwnd(), true)
            .map_err(|error| format!("PROBE_DDA_EXCLUDE_ENABLE: {error}"))?;
        overlay.show()?;
        let trigger = ProbeWindow::create(trigger_rect, TRIGGER)?;
        trigger.show()?;
        trigger.move_by(2)?;
        let excluded = wait_for_dda_color(
            &mut session,
            &device,
            &context,
            source_roi,
            CYAN,
            Duration::from_secs(2),
        )?;
        let ordinary = ordinary_capture_stats(sample_rect)?;

        set_window_excluded_from_dda(overlay.hwnd(), false)
            .map_err(|error| format!("PROBE_DDA_EXCLUDE_DISABLE: {error}"))?;
        unsafe {
            if !InvalidateRect(Some(overlay.hwnd()), None, true).as_bool() {
                return Err(format!(
                    "PROBE_OVERLAY_INVALIDATE: {}",
                    windows::core::Error::from_thread()
                ));
            }
            if !UpdateWindow(overlay.hwnd()).as_bool() {
                return Err(format!(
                    "PROBE_OVERLAY_UPDATE: {}",
                    windows::core::Error::from_thread()
                ));
            }
            DwmFlush().map_err(|error| format!("PROBE_DWM_FLUSH: {error}"))?;
        }
        trigger.move_by(-2)?;
        let included = wait_for_dda_color(
            &mut session,
            &device,
            &context,
            source_roi,
            MAGENTA,
            Duration::from_secs(2),
        )?;

        let duplication_format = session.format().map(|format| format.name());
        let duplication_rotation = session.description().Rotation.0;
        drop(session);
        set_window_excluded_from_dda(overlay.hwnd(), true)
            .map_err(|error| format!("PROBE_DDA_SOURCE_EXCLUDE: {error}"))?;
        trigger.move_by(2)?;
        let mut texture_source = DesktopTextureSource::new_with_device_output(
            binding.clone(),
            device.clone(),
            context.clone(),
        )
        .map_err(|error| format!("PROBE_TEXTURE_SOURCE_CREATE: {error}"))?;
        let source_deadline = Instant::now() + Duration::from_secs(2);
        let source_frame = loop {
            if Instant::now() >= source_deadline {
                return Err("PROBE_TEXTURE_SOURCE_TIMEOUT".to_owned());
            }
            match texture_source.poll(100) {
                Ok(DesktopTexturePoll::Updated(frame)) => break frame,
                Ok(DesktopTexturePoll::NoFrame) => {}
                Ok(DesktopTexturePoll::Recovering { retry_after, .. }) => {
                    thread::sleep(retry_after.min(Duration::from_millis(100)));
                }
                Err(error) => return Err(format!("PROBE_TEXTURE_SOURCE_POLL: {error}")),
            }
        };
        background.set_color(GREEN)?;
        let stationary_dynamic = wait_for_texture_source_color(
            &mut texture_source,
            &device,
            &context,
            source_roi,
            GREEN,
            Duration::from_secs(2),
        )?;

        let passed = baseline.cyan_ratio >= 0.90
            && excluded.cyan_ratio >= 0.90
            && excluded.magenta_ratio <= 0.02
            && ordinary.magenta_ratio >= 0.90
            && included.magenta_ratio >= 0.90
            && stationary_dynamic.green_ratio >= 0.90;
        Ok(json!({
            "passed": passed,
            "adapter": {
                "index": binding.adapter_index(),
                "luid": binding.adapter_luid_string(),
                "name": binding.adapter_name(),
            },
            "output": {
                "index": binding.output_index(),
                "name": binding.output_name(),
                "desktop": [desktop.left, desktop.top, desktop.right, desktop.bottom],
                "format": duplication_format,
                "rotation": duplication_rotation,
            },
            "sample_rect": [sample_rect.left, sample_rect.top, sample_rect.right, sample_rect.bottom],
            "dda_exclusion": {
                "baseline": baseline,
                "excluded": excluded,
                "included_negative_control": included,
            },
            "ordinary_capture": ordinary,
            "desktop_texture_source": {
                "ready": texture_source.texture().is_some(),
                "generation": texture_source.texture_generation(),
                "source_format": source_frame.source_format.name(),
                "dirty_rects": source_frame.dirty_rect_count,
                "move_rects": source_frame.move_rect_count,
                "copied_full_frame": source_frame.copied_full_frame,
                "access_lost_count": texture_source.access_lost_count(),
                "stationary_dynamic_background": stationary_dynamic,
            },
            "pixels_persisted": false,
        }))
    }

    fn wait_for_texture_source_color(
        source: &mut DesktopTextureSource,
        device: &ID3D11Device,
        context: &ID3D11DeviceContext,
        roi: RECT,
        expected: Rgb,
        timeout: Duration,
    ) -> Result<PixelStats, String> {
        let deadline = Instant::now() + timeout;
        let mut latest = None;
        while Instant::now() < deadline {
            match source.poll(100) {
                Ok(DesktopTexturePoll::Updated(_)) => {
                    let texture = source
                        .texture()
                        .ok_or_else(|| "PROBE_TEXTURE_SOURCE_MISSING".to_owned())?;
                    let (bytes, order) = read_texture_roi(device, context, texture, roi)?;
                    let stats = pixel_stats(&bytes, order);
                    let ratio = if expected.r == GREEN.r && expected.g == GREEN.g {
                        stats.green_ratio
                    } else if expected.r == MAGENTA.r && expected.g == MAGENTA.g {
                        stats.magenta_ratio
                    } else {
                        stats.cyan_ratio
                    };
                    latest = Some(stats);
                    if ratio >= 0.90 {
                        return Ok(latest.expect("latest sample exists"));
                    }
                }
                Ok(DesktopTexturePoll::NoFrame) => {}
                Ok(DesktopTexturePoll::Recovering { retry_after, .. }) => {
                    thread::sleep(retry_after.min(Duration::from_millis(100)));
                }
                Err(error) => return Err(format!("PROBE_TEXTURE_SOURCE_POLL: {error}")),
            }
        }
        latest.ok_or_else(|| "PROBE_TEXTURE_SOURCE_DYNAMIC_TIMEOUT".to_owned())
    }

    fn wait_for_dda_color(
        session: &mut DuplicationSession,
        device: &ID3D11Device,
        context: &ID3D11DeviceContext,
        roi: RECT,
        expected: Rgb,
        timeout: Duration,
    ) -> Result<PixelStats, String> {
        let deadline = Instant::now() + timeout;
        let mut latest = None;
        while Instant::now() < deadline {
            match session.acquire_next_frame(100) {
                Ok(frame) => {
                    let (bytes, order) = read_texture_roi(device, context, frame.texture(), roi)?;
                    drop(frame);
                    let stats = pixel_stats(&bytes, order);
                    let ratio = if expected.r == MAGENTA.r && expected.g == MAGENTA.g {
                        stats.magenta_ratio
                    } else {
                        stats.cyan_ratio
                    };
                    latest = Some(stats);
                    if ratio >= 0.90 {
                        return Ok(latest.expect("latest sample exists"));
                    }
                }
                Err(fairy_windows_capture_dda::DdaError::Timeout) => continue,
                Err(error) => return Err(format!("PROBE_ACQUIRE_FRAME: {error}")),
            }
        }
        latest.ok_or_else(|| "PROBE_NO_DESKTOP_FRAME".to_owned())
    }

    fn read_texture_roi(
        device: &ID3D11Device,
        context: &ID3D11DeviceContext,
        source: &ID3D11Texture2D,
        roi: RECT,
    ) -> Result<(Vec<u8>, PixelOrder), String> {
        let width =
            u32::try_from(roi.right - roi.left).map_err(|_| "PROBE_ROI_INVALID".to_owned())?;
        let height =
            u32::try_from(roi.bottom - roi.top).map_err(|_| "PROBE_ROI_INVALID".to_owned())?;
        let mut source_desc = D3D11_TEXTURE2D_DESC::default();
        unsafe { source.GetDesc(&mut source_desc) };
        let order = match source_desc.Format {
            DXGI_FORMAT_B8G8R8A8_UNORM => PixelOrder::Bgra8,
            DXGI_FORMAT_R10G10B10A2_UNORM => PixelOrder::Rgb10A2,
            DXGI_FORMAT_R16G16B16A16_FLOAT => PixelOrder::Rgba16Float,
            format => return Err(format!("PROBE_SOURCE_FORMAT_UNSUPPORTED: {}", format.0)),
        };
        let left = u32::try_from(roi.left).map_err(|_| "PROBE_ROI_INVALID".to_owned())?;
        let top = u32::try_from(roi.top).map_err(|_| "PROBE_ROI_INVALID".to_owned())?;
        if left + width > source_desc.Width || top + height > source_desc.Height {
            return Err("PROBE_ROI_OUT_OF_BOUNDS".to_owned());
        }
        let staging_desc = D3D11_TEXTURE2D_DESC {
            Width: width,
            Height: height,
            MipLevels: 1,
            ArraySize: 1,
            Format: source_desc.Format,
            SampleDesc: source_desc.SampleDesc,
            Usage: D3D11_USAGE_STAGING,
            BindFlags: 0,
            CPUAccessFlags: D3D11_CPU_ACCESS_READ.0 as u32,
            MiscFlags: 0,
        };
        let mut staging = None;
        unsafe { device.CreateTexture2D(&staging_desc, None, Some(&mut staging)) }
            .map_err(|error| format!("PROBE_STAGING_TEXTURE: {error}"))?;
        let staging = staging.ok_or_else(|| "PROBE_STAGING_TEXTURE_MISSING".to_owned())?;
        let source_box = D3D11_BOX {
            left,
            top,
            front: 0,
            right: left + width,
            bottom: top + height,
            back: 1,
        };
        let staging_resource: ID3D11Resource = staging
            .cast()
            .map_err(|error| format!("PROBE_STAGING_RESOURCE: {error}"))?;
        let source_resource: ID3D11Resource = source
            .cast()
            .map_err(|error| format!("PROBE_SOURCE_RESOURCE: {error}"))?;
        unsafe {
            context.CopySubresourceRegion(
                Some(&staging_resource),
                0,
                0,
                0,
                0,
                Some(&source_resource),
                0,
                Some(&source_box),
            );
        }
        let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
        unsafe {
            context
                .Map(
                    Some(&staging_resource),
                    0,
                    D3D11_MAP_READ,
                    0,
                    Some(&mut mapped),
                )
                .map_err(|error| format!("PROBE_STAGING_MAP: {error}"))?;
        }
        let bytes_per_pixel = order.bytes_per_pixel();
        let row_bytes = width as usize * bytes_per_pixel;
        let mut bytes = vec![0_u8; row_bytes * height as usize];
        let source_pointer = mapped.pData.cast::<u8>();
        for row in 0..height as usize {
            let source_row = unsafe {
                std::slice::from_raw_parts(
                    source_pointer.add(row * mapped.RowPitch as usize),
                    row_bytes,
                )
            };
            let destination = &mut bytes[row * row_bytes..(row + 1) * row_bytes];
            destination.copy_from_slice(source_row);
        }
        unsafe { context.Unmap(Some(&staging_resource), 0) };
        Ok((bytes, order))
    }

    fn ordinary_capture_stats(sample_rect: RECT) -> Result<PixelStats, String> {
        let center_x = sample_rect.left + (sample_rect.right - sample_rect.left) / 2;
        let center_y = sample_rect.top + (sample_rect.bottom - sample_rect.top) / 2;
        let monitor = Monitor::from_point(center_x, center_y)
            .map_err(|error| format!("PROBE_SCREENSHOT_MONITOR: {error}"))?;
        let monitor_x = monitor
            .x()
            .map_err(|error| format!("PROBE_SCREENSHOT_MONITOR_X: {error}"))?;
        let monitor_y = monitor
            .y()
            .map_err(|error| format!("PROBE_SCREENSHOT_MONITOR_Y: {error}"))?;
        let relative_x = u32::try_from(sample_rect.left - monitor_x)
            .map_err(|_| "PROBE_SCREENSHOT_ROI_INVALID".to_owned())?;
        let relative_y = u32::try_from(sample_rect.top - monitor_y)
            .map_err(|_| "PROBE_SCREENSHOT_ROI_INVALID".to_owned())?;
        let width = u32::try_from(sample_rect.right - sample_rect.left)
            .map_err(|_| "PROBE_SCREENSHOT_ROI_INVALID".to_owned())?;
        let height = u32::try_from(sample_rect.bottom - sample_rect.top)
            .map_err(|_| "PROBE_SCREENSHOT_ROI_INVALID".to_owned())?;
        let image = monitor
            .capture_region(relative_x, relative_y, width, height)
            .map_err(|error| format!("PROBE_SCREENSHOT_CAPTURE: {error}"))?;
        Ok(pixel_stats(image.as_raw(), PixelOrder::Rgba8))
    }

    #[derive(Clone, Copy)]
    enum PixelOrder {
        Bgra8,
        Rgba8,
        Rgb10A2,
        Rgba16Float,
    }

    impl PixelOrder {
        const fn bytes_per_pixel(self) -> usize {
            match self {
                Self::Rgba16Float => 8,
                Self::Bgra8 | Self::Rgba8 | Self::Rgb10A2 => 4,
            }
        }
    }

    fn pixel_stats(bytes: &[u8], order: PixelOrder) -> PixelStats {
        let mut cyan = 0_u64;
        let mut magenta = 0_u64;
        let mut green = 0_u64;
        let mut black = 0_u64;
        let mut sum = [0_u64; 3];
        let mut count = 0_u64;
        for pixel in bytes.chunks_exact(order.bytes_per_pixel()) {
            let (r, g, b) = match order {
                PixelOrder::Bgra8 => (pixel[2], pixel[1], pixel[0]),
                PixelOrder::Rgba8 => (pixel[0], pixel[1], pixel[2]),
                PixelOrder::Rgb10A2 => decode_rgb10a2(pixel),
                PixelOrder::Rgba16Float => decode_rgba16_float(pixel),
            };
            count += 1;
            sum[0] += u64::from(r);
            sum[1] += u64::from(g);
            sum[2] += u64::from(b);
            cyan += u64::from(color_matches(r, g, b, CYAN));
            magenta += u64::from(color_matches(r, g, b, MAGENTA));
            green += u64::from(color_matches(r, g, b, GREEN));
            black += u64::from(r < 12 && g < 12 && b < 12);
        }
        let denominator = count.max(1) as f64;
        PixelStats {
            sha256: format!("{:x}", Sha256::digest(bytes)),
            cyan_ratio: cyan as f64 / denominator,
            magenta_ratio: magenta as f64 / denominator,
            green_ratio: green as f64 / denominator,
            black_ratio: black as f64 / denominator,
            mean_rgb: [
                sum[0] as f64 / denominator,
                sum[1] as f64 / denominator,
                sum[2] as f64 / denominator,
            ],
        }
    }

    fn decode_rgb10a2(pixel: &[u8]) -> (u8, u8, u8) {
        let packed = u32::from_le_bytes(pixel[..4].try_into().expect("four byte pixel"));
        let r = packed & 0x3ff;
        let g = (packed >> 10) & 0x3ff;
        let b = (packed >> 20) & 0x3ff;
        (
            ((r * 255 + 511) / 1023) as u8,
            ((g * 255 + 511) / 1023) as u8,
            ((b * 255 + 511) / 1023) as u8,
        )
    }

    fn decode_rgba16_float(pixel: &[u8]) -> (u8, u8, u8) {
        let channel = |offset: usize| {
            let bits = u16::from_le_bytes([pixel[offset], pixel[offset + 1]]);
            linear_to_srgb_u8(half_to_f32(bits))
        };
        (channel(0), channel(2), channel(4))
    }

    fn linear_to_srgb_u8(value: f32) -> u8 {
        let value = value.clamp(0.0, 1.0);
        let encoded = if value <= 0.003_130_8 {
            value * 12.92
        } else {
            1.055 * value.powf(1.0 / 2.4) - 0.055
        };
        (encoded * 255.0).round().clamp(0.0, 255.0) as u8
    }

    fn half_to_f32(bits: u16) -> f32 {
        let sign = ((bits >> 15) & 1) as u32;
        let exponent = ((bits >> 10) & 0x1f) as u32;
        let fraction = (bits & 0x03ff) as u32;
        let converted = match exponent {
            0 if fraction == 0 => sign << 31,
            0 => {
                let leading = fraction.leading_zeros() - 22;
                let normalized_fraction = (fraction << (leading + 1)) & 0x03ff;
                let normalized_exponent = 127 - 15 - leading;
                (sign << 31) | (normalized_exponent << 23) | (normalized_fraction << 13)
            }
            0x1f => (sign << 31) | 0x7f80_0000 | (fraction << 13),
            _ => (sign << 31) | ((exponent + 127 - 15) << 23) | (fraction << 13),
        };
        f32::from_bits(converted)
    }

    fn color_matches(r: u8, g: u8, b: u8, expected: Rgb) -> bool {
        r.abs_diff(expected.r) <= 24 && g.abs_diff(expected.g) <= 24 && b.abs_diff(expected.b) <= 24
    }

    fn centered_rect(bounds: RECT, width: i32, height: i32) -> RECT {
        let left = bounds.left + (bounds.right - bounds.left - width) / 2;
        let top = bounds.top + (bounds.bottom - bounds.top - height) / 2;
        RECT {
            left,
            top,
            right: left + width,
            bottom: top + height,
        }
    }

    fn inset_rect(rect: RECT, inset: i32) -> RECT {
        RECT {
            left: rect.left + inset,
            top: rect.top + inset,
            right: rect.right - inset,
            bottom: rect.bottom - inset,
        }
    }

    fn output_relative_rect(rect: RECT, output: RECT) -> Result<RECT, String> {
        if rect.left < output.left
            || rect.top < output.top
            || rect.right > output.right
            || rect.bottom > output.bottom
        {
            return Err("PROBE_ROI_OUTSIDE_OUTPUT".to_owned());
        }
        Ok(RECT {
            left: rect.left - output.left,
            top: rect.top - output.top,
            right: rect.right - output.left,
            bottom: rect.bottom - output.top,
        })
    }

    fn register_window_class() -> Result<(), String> {
        static REGISTERED: std::sync::OnceLock<Result<(), String>> = std::sync::OnceLock::new();
        REGISTERED
            .get_or_init(|| {
                let module = unsafe { GetModuleHandleW(PCWSTR::null()) }
                    .map_err(|error| format!("PROBE_CLASS_MODULE: {error}"))?;
                let class = WNDCLASSW {
                    style: CS_HREDRAW | CS_VREDRAW,
                    lpfnWndProc: Some(probe_window_proc),
                    hInstance: HINSTANCE(module.0),
                    lpszClassName: WINDOW_CLASS,
                    ..WNDCLASSW::default()
                };
                if unsafe { RegisterClassW(&class) } == 0 {
                    return Err(format!(
                        "PROBE_CLASS_REGISTER: {}",
                        windows::core::Error::from_thread()
                    ));
                }
                Ok(())
            })
            .clone()
    }

    unsafe extern "system" fn probe_window_proc(
        hwnd: HWND,
        message: u32,
        wparam: WPARAM,
        lparam: LPARAM,
    ) -> LRESULT {
        match message {
            WM_ERASEBKGND => LRESULT(1),
            WM_PAINT => {
                let mut paint = PAINTSTRUCT::default();
                let dc = unsafe { BeginPaint(hwnd, &mut paint) };
                let mut rect = RECT::default();
                let _ = unsafe { GetClientRect(hwnd, &mut rect) };
                let color = unsafe { GetWindowLongPtrW(hwnd, GWLP_USERDATA) } as u32;
                let brush = unsafe { CreateSolidBrush(COLORREF(color)) };
                unsafe {
                    FillRect(dc, &rect, brush);
                    let _ = DeleteObject(HGDIOBJ(brush.0));
                    let _ = EndPaint(hwnd, &paint);
                }
                LRESULT(0)
            }
            _ => unsafe { DefWindowProcW(hwnd, message, wparam, lparam) },
        }
    }

    fn pump_messages() {
        let mut message = MSG::default();
        unsafe {
            while PeekMessageW(&mut message, None, 0, 0, PM_REMOVE).as_bool() {
                let _ = TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
    }
}

#[cfg(target_os = "windows")]
fn main() {
    windows_probe::main();
}
