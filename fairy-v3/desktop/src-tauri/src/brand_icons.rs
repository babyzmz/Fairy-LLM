//! Tauri decodes only ICO entry zero for window icons. Load the native resource
//! at the window's actual DPI instead of stretching that 16px image.
use std::sync::Mutex;

use tauri::{Manager, WindowEvent};
use windows_sys::Win32::{
    Foundation::HWND,
    System::LibraryLoader::GetModuleHandleW,
    UI::{
        HiDpi::{GetDpiForWindow, GetSystemMetricsForDpi},
        WindowsAndMessaging::{
            DestroyIcon, LoadImageW, SendMessageW, HICON, ICON_BIG, ICON_SMALL, IMAGE_ICON,
            SM_CXICON, SM_CXSMICON, SM_CYICON, SM_CYSMICON, WM_SETICON,
        },
    },
};

// tauri-build / winresource's default executable icon resource identifier.
const APP_ICON_RESOURCE: usize = 32512;

// Store the handle value, not a borrowed pointer. Only this owner destroys it;
// HICON destruction is thread-independent. No LR_SHARED size-cache aliasing.
struct OwnedIcon(usize);

impl Drop for OwnedIcon {
    fn drop(&mut self) {
        unsafe { DestroyIcon(self.0 as HICON) };
    }
}

fn load_icon(width: i32, height: i32) -> std::io::Result<OwnedIcon> {
    let icon = unsafe {
        LoadImageW(
            GetModuleHandleW(std::ptr::null()),
            APP_ICON_RESOURCE as *const u16,
            IMAGE_ICON,
            width,
            height,
            0,
        )
    };
    if icon.is_null() {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(OwnedIcon(icon as usize))
    }
}

fn load_pair(
    dpi: u32,
    load: impl Fn(i32, i32) -> std::io::Result<OwnedIcon>,
) -> std::io::Result<[OwnedIcon; 2]> {
    unsafe {
        Ok([
            load(
                GetSystemMetricsForDpi(SM_CXSMICON, dpi),
                GetSystemMetricsForDpi(SM_CYSMICON, dpi),
            )?,
            load(
                GetSystemMetricsForDpi(SM_CXICON, dpi),
                GetSystemMetricsForDpi(SM_CYICON, dpi),
            )?,
        ])
    }
}

fn refresh(hwnd: HWND, owned: &Mutex<Option<[OwnedIcon; 2]>>) -> std::io::Result<()> {
    let dpi = unsafe { GetDpiForWindow(hwnd) };
    let pair = load_pair(if dpi == 0 { 96 } else { dpi }, load_icon)?;
    let mut previous = owned.lock().unwrap_or_else(|error| error.into_inner());
    unsafe {
        SendMessageW(hwnd, WM_SETICON, ICON_SMALL as usize, pair[0].0 as isize);
        SendMessageW(hwnd, WM_SETICON, ICON_BIG as usize, pair[1].0 as isize);
    }
    // Tauri owns the old default handles. Drop only our own replaced pair,
    // after Windows no longer references it.
    *previous = Some(pair);
    Ok(())
}

pub fn install(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let Some(window) = app.get_webview_window("main") else {
        return Ok(());
    };
    let hwnd = window.hwnd()?.0 as usize;
    let owned = Mutex::new(None);
    refresh(hwnd as HWND, &owned)?;
    window.on_window_event(move |event| match event {
        WindowEvent::ScaleFactorChanged { .. } => {
            if let Err(error) = refresh(hwnd as HWND, &owned) {
                eprintln!("window icon DPI refresh failed: {error}");
            }
        }
        WindowEvent::Destroyed => {
            owned
                .lock()
                .unwrap_or_else(|error| error.into_inner())
                .take();
        }
        _ => {}
    });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use windows_sys::Win32::{
        Graphics::Gdi::{DeleteObject, GetObjectW, BITMAP},
        UI::WindowsAndMessaging::{GetIconInfo, ICONINFO, LR_LOADFROMFILE},
    };

    fn dimensions(icon: &OwnedIcon) -> (i32, i32) {
        unsafe {
            let mut info: ICONINFO = std::mem::zeroed();
            assert_ne!(GetIconInfo(icon.0 as HICON, &mut info), 0);
            let mut bitmap: BITMAP = std::mem::zeroed();
            let result = GetObjectW(
                info.hbmColor,
                std::mem::size_of::<BITMAP>() as i32,
                &mut bitmap as *mut _ as *mut _,
            );
            DeleteObject(info.hbmColor);
            DeleteObject(info.hbmMask);
            assert_ne!(result, 0);
            (bitmap.bmWidth, bitmap.bmHeight)
        }
    }

    #[test]
    fn ico_frames_match_native_titlebar_and_taskbar_dpi_sizes() {
        // Cargo's lib test executable has no RC section. Use the same ICO via
        // the real Windows loader; executable embedding is verified in Tauri.
        let path: Vec<u16> = concat!(env!("CARGO_MANIFEST_DIR"), "/icons/icon.ico")
            .encode_utf16()
            .chain(Some(0))
            .collect();
        for (dpi, small, big) in [(96, 16, 32), (120, 20, 40), (144, 24, 48), (192, 32, 64)] {
            let icons = load_pair(dpi, |width, height| {
                let icon = unsafe {
                    LoadImageW(
                        std::ptr::null_mut(),
                        path.as_ptr(),
                        IMAGE_ICON,
                        width,
                        height,
                        LR_LOADFROMFILE,
                    )
                };
                assert!(!icon.is_null(), "multi-size ICO must load");
                Ok(OwnedIcon(icon as usize))
            })
            .expect("native DPI icon pair");
            assert_eq!(dimensions(&icons[0]), (small, small));
            assert_eq!(dimensions(&icons[1]), (big, big));
        }
    }
}
