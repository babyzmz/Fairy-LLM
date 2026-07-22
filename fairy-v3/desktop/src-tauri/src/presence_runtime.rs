use serde::Serialize;

pub const PRESENCE_RUNTIME_POLICY_EVENT: &str = "presence-runtime-policy";

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct PresenceRuntimePolicy {
    pub schema_version: u16,
    pub frame_rate_limit: u16,
    pub power_saver: bool,
    pub foreground_fullscreen: bool,
}

pub fn resolve_frame_rate_limit(power_saver: bool, foreground_fullscreen: bool) -> u16 {
    if power_saver || foreground_fullscreen {
        15
    } else {
        300
    }
}

pub fn sample_presence_runtime_policy() -> PresenceRuntimePolicy {
    let power_saver = system_power_saver_enabled();
    let foreground_fullscreen = foreground_window_is_fullscreen();
    PresenceRuntimePolicy {
        schema_version: 1,
        frame_rate_limit: resolve_frame_rate_limit(power_saver, foreground_fullscreen),
        power_saver,
        foreground_fullscreen,
    }
}

#[cfg(target_os = "windows")]
fn system_power_saver_enabled() -> bool {
    use windows_sys::Win32::System::Power::{GetSystemPowerStatus, SYSTEM_POWER_STATUS};

    let mut status = SYSTEM_POWER_STATUS::default();
    unsafe { GetSystemPowerStatus(&mut status) != 0 && status.SystemStatusFlag == 1 }
}

#[cfg(not(target_os = "windows"))]
fn system_power_saver_enabled() -> bool {
    false
}

#[cfg(target_os = "windows")]
fn foreground_window_is_fullscreen() -> bool {
    use std::mem::size_of;

    use windows_sys::Win32::Foundation::RECT;
    use windows_sys::Win32::Graphics::Gdi::{
        GetMonitorInfoW, MonitorFromWindow, MONITORINFO, MONITOR_DEFAULTTONEAREST,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowRect};

    unsafe {
        let window = GetForegroundWindow();
        if window.is_null() {
            return false;
        }
        let mut window_rect = RECT::default();
        if GetWindowRect(window, &mut window_rect) == 0 {
            return false;
        }
        let monitor = MonitorFromWindow(window, MONITOR_DEFAULTTONEAREST);
        if monitor.is_null() {
            return false;
        }
        let mut info = MONITORINFO {
            cbSize: size_of::<MONITORINFO>() as u32,
            ..MONITORINFO::default()
        };
        if GetMonitorInfoW(monitor, &mut info) == 0 {
            return false;
        }
        rect_covers_monitor(window_rect, info.rcMonitor)
    }
}

#[cfg(target_os = "windows")]
fn rect_covers_monitor(
    window: windows_sys::Win32::Foundation::RECT,
    monitor: windows_sys::Win32::Foundation::RECT,
) -> bool {
    const TOLERANCE_PX: i32 = 2;
    window.left <= monitor.left + TOLERANCE_PX
        && window.top <= monitor.top + TOLERANCE_PX
        && window.right >= monitor.right - TOLERANCE_PX
        && window.bottom >= monitor.bottom - TOLERANCE_PX
}

#[cfg(not(target_os = "windows"))]
fn foreground_window_is_fullscreen() -> bool {
    false
}

#[cfg(test)]
mod tests {
    use super::resolve_frame_rate_limit;

    #[test]
    fn power_saver_and_fullscreen_content_limit_foreground_animation_work() {
        assert_eq!(resolve_frame_rate_limit(false, false), 300);
        assert_eq!(resolve_frame_rate_limit(true, false), 15);
        assert_eq!(resolve_frame_rate_limit(false, true), 15);
        assert_eq!(resolve_frame_rate_limit(true, true), 15);
    }
}
