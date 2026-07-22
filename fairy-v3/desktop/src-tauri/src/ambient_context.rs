use serde::Serialize;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct AmbientDeviceFacts {
    pub user_idle_seconds: u32,
    pub battery_percent: Option<u8>,
    pub charging: Option<bool>,
    pub foreground_fullscreen: bool,
    pub locked: bool,
    pub microphone_active: bool,
    pub do_not_disturb: bool,
}

pub fn sample_ambient_device_facts() -> AmbientDeviceFacts {
    let (battery_percent, charging) = battery_status();
    AmbientDeviceFacts {
        user_idle_seconds: user_idle_seconds(),
        battery_percent,
        charging,
        foreground_fullscreen: crate::presence_runtime::foreground_window_is_fullscreen(),
        locked: session_appears_locked(),
        microphone_active: microphone_session_active(),
        do_not_disturb: system_do_not_disturb(),
    }
}

#[cfg(target_os = "windows")]
fn user_idle_seconds() -> u32 {
    use std::mem::size_of;

    use windows_sys::Win32::System::SystemInformation::GetTickCount;
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{GetLastInputInfo, LASTINPUTINFO};

    let mut input = LASTINPUTINFO {
        cbSize: size_of::<LASTINPUTINFO>() as u32,
        dwTime: 0,
    };
    if unsafe { GetLastInputInfo(&mut input) } == 0 {
        return 0;
    }
    idle_seconds_from_ticks(unsafe { GetTickCount() }, input.dwTime)
}

#[cfg(not(target_os = "windows"))]
fn user_idle_seconds() -> u32 {
    0
}

fn idle_seconds_from_ticks(now: u32, last_input: u32) -> u32 {
    now.wrapping_sub(last_input) / 1_000
}

#[cfg(target_os = "windows")]
fn session_appears_locked() -> bool {
    use windows_sys::Win32::UI::WindowsAndMessaging::GetForegroundWindow;

    unsafe { GetForegroundWindow().is_null() }
}

#[cfg(not(target_os = "windows"))]
fn session_appears_locked() -> bool {
    false
}

#[cfg(target_os = "windows")]
fn battery_status() -> (Option<u8>, Option<bool>) {
    use windows_sys::Win32::System::Power::{GetSystemPowerStatus, SYSTEM_POWER_STATUS};

    let mut status = SYSTEM_POWER_STATUS::default();
    if unsafe { GetSystemPowerStatus(&mut status) } == 0 {
        return (None, None);
    }
    let percentage = (status.BatteryLifePercent != u8::MAX).then_some(status.BatteryLifePercent);
    let charging = match status.ACLineStatus {
        0 => Some(false),
        1 => Some(true),
        _ => None,
    };
    (percentage, charging)
}

#[cfg(target_os = "windows")]
fn system_do_not_disturb() -> bool {
    use windows::Win32::UI::Shell::SHQueryUserNotificationState;

    unsafe { SHQueryUserNotificationState() }
        .map(|state| notification_state_suppresses_dialogue(state.0))
        .unwrap_or(false)
}

#[cfg(not(target_os = "windows"))]
fn system_do_not_disturb() -> bool {
    false
}

const fn notification_state_suppresses_dialogue(state: i32) -> bool {
    // QUNS_ACCEPTS_NOTIFICATIONS is the only state in which Windows explicitly
    // indicates that an unsolicited notification is appropriate.
    state != 5
}

#[cfg(target_os = "windows")]
fn microphone_session_active() -> bool {
    use windows::Win32::Foundation::RPC_E_CHANGED_MODE;
    use windows::Win32::System::Com::{CoInitializeEx, CoUninitialize, COINIT_MULTITHREADED};

    let initialization = unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) };
    if initialization.is_err() && initialization != RPC_E_CHANGED_MODE {
        return false;
    }
    let active = unsafe { enumerate_active_capture_sessions().unwrap_or(false) };
    if initialization.is_ok() {
        unsafe { CoUninitialize() };
    }
    active
}

#[cfg(target_os = "windows")]
unsafe fn enumerate_active_capture_sessions() -> windows::core::Result<bool> {
    use windows::Win32::Media::Audio::{
        eCapture, AudioSessionStateActive, IAudioSessionManager2, IMMDeviceEnumerator,
        MMDeviceEnumerator, DEVICE_STATE_ACTIVE,
    };
    use windows::Win32::System::Com::{CoCreateInstance, CLSCTX_ALL};

    let enumerator: IMMDeviceEnumerator =
        unsafe { CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL)? };
    let devices = unsafe { enumerator.EnumAudioEndpoints(eCapture, DEVICE_STATE_ACTIVE)? };
    for device_index in 0..unsafe { devices.GetCount()? } {
        let Ok(device) = (unsafe { devices.Item(device_index) }) else {
            continue;
        };
        let Ok(manager) = (unsafe { device.Activate::<IAudioSessionManager2>(CLSCTX_ALL, None) })
        else {
            continue;
        };
        let Ok(sessions) = (unsafe { manager.GetSessionEnumerator() }) else {
            continue;
        };
        for session_index in 0..unsafe { sessions.GetCount()? } {
            let Ok(session) = (unsafe { sessions.GetSession(session_index) }) else {
                continue;
            };
            if unsafe { session.GetState()? } == AudioSessionStateActive {
                return Ok(true);
            }
        }
    }
    Ok(false)
}

#[cfg(not(target_os = "windows"))]
fn microphone_session_active() -> bool {
    false
}

#[cfg(not(target_os = "windows"))]
fn battery_status() -> (Option<u8>, Option<bool>) {
    (None, None)
}

#[cfg(test)]
mod tests {
    use super::{idle_seconds_from_ticks, notification_state_suppresses_dialogue};

    #[test]
    fn idle_duration_handles_windows_tick_counter_wraparound() {
        assert_eq!(idle_seconds_from_ticks(15_000, 10_000), 5);
        assert_eq!(idle_seconds_from_ticks(2_000, u32::MAX - 997), 2);
    }

    #[test]
    fn ambient_dialogue_requires_windows_to_accept_notifications() {
        assert!(!notification_state_suppresses_dialogue(5));
        for state in [1, 2, 3, 4, 6, 7] {
            assert!(notification_state_suppresses_dialogue(state));
        }
    }
}
