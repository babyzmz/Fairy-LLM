//! DDA exclusions belong to renderer leases, not to the lifetime of a Tauri HWND.
use std::collections::HashMap;
use std::ffi::c_void;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, OnceLock};

use fairy_windows_capture_dda::set_window_excluded_from_dda;
use windows::core::{s, w, BOOL};
use windows::Win32::Foundation::HWND;
use windows::Win32::System::LibraryLoader::{GetModuleHandleW, GetProcAddress};

const PROPERTY: &[u16] = &[
    70, 97, 105, 114, 121, 46, 68, 68, 65, 46, 79, 119, 110, 101, 114, 46, 86, 49, 0,
];
static NEXT_OWNER: AtomicUsize = AtomicUsize::new(1);
static OWNERS: OnceLock<Mutex<HashMap<isize, Ownership>>> = OnceLock::new();

#[link(name = "user32")]
unsafe extern "system" {
    fn GetPropW(hwnd: isize, name: *const u16) -> isize;
    fn SetPropW(hwnd: isize, name: *const u16, value: isize) -> i32;
    fn RemovePropW(hwnd: isize, name: *const u16) -> isize;
}

struct Ownership {
    token: usize,
    users: usize,
    previous: bool,
}

pub(super) struct DdaExclusionLease {
    hwnd: isize,
    token: usize,
}

impl DdaExclusionLease {
    pub(super) fn acquire(hwnd: isize) -> Result<Self, String> {
        let mut owners = OWNERS
            .get_or_init(Mutex::default)
            .lock()
            .map_err(|_| "PRESENCE_DDA_OWNERSHIP_LOCK_FAILED".to_owned())?;
        let property = unsafe { GetPropW(hwnd, PROPERTY.as_ptr()) } as usize;
        if let Some(owner) = owners.get_mut(&hwnd) {
            if property == owner.token {
                owner.users += 1;
                return Ok(Self {
                    hwnd,
                    token: owner.token,
                });
            }
            // A destroyed/recreated HWND has no old ownership marker.
            owners.remove(&hwnd);
        }
        let previous = previous_exclusion(HWND(hwnd as *mut c_void))?;
        let token = NEXT_OWNER.fetch_add(1, Ordering::Relaxed).max(1);
        if unsafe { SetPropW(hwnd, PROPERTY.as_ptr(), token as isize) } == 0 {
            return Err("PRESENCE_DDA_OWNERSHIP_MARKER_FAILED".to_owned());
        }
        if let Err(error) = set_window_excluded_from_dda(HWND(hwnd as *mut c_void), true) {
            unsafe {
                RemovePropW(hwnd, PROPERTY.as_ptr());
            }
            return Err(error.to_string());
        }
        owners.insert(
            hwnd,
            Ownership {
                token,
                users: 1,
                previous,
            },
        );
        Ok(Self { hwnd, token })
    }
}

impl Drop for DdaExclusionLease {
    fn drop(&mut self) {
        let Ok(mut owners) = OWNERS.get_or_init(Mutex::default).lock() else {
            return;
        };
        let Some(owner) = owners.get_mut(&self.hwnd) else {
            return;
        };
        if owner.token != self.token {
            return;
        }
        if unsafe { GetPropW(self.hwnd, PROPERTY.as_ptr()) } as usize != self.token {
            owners.remove(&self.hwnd);
            return;
        }
        owner.users -= 1;
        if owner.users != 0 {
            return;
        }
        let previous = owner.previous;
        if let Err(error) = set_window_excluded_from_dda(HWND(self.hwnd as *mut c_void), previous) {
            eprintln!("DDA exclusion restore failed: {error}");
        }
        unsafe {
            RemovePropW(self.hwnd, PROPERTY.as_ptr());
        }
        owners.remove(&self.hwnd);
    }
}

fn previous_exclusion(hwnd: HWND) -> Result<bool, String> {
    #[repr(C)]
    struct Attribute {
        kind: i32,
        data: *mut c_void,
        size: usize,
    }
    type GetAttribute = unsafe extern "system" fn(HWND, *mut Attribute) -> BOOL;
    let module = unsafe { GetModuleHandleW(w!("user32.dll")) }
        .map_err(|_| "PRESENCE_DDA_ATTRIBUTE_UNAVAILABLE".to_owned())?;
    let procedure = unsafe { GetProcAddress(module, s!("GetWindowCompositionAttribute")) }
        .ok_or_else(|| "PRESENCE_DDA_ATTRIBUTE_UNAVAILABLE".to_owned())?;
    let get: GetAttribute = unsafe { std::mem::transmute(procedure) };
    let mut value = BOOL(0);
    let mut attribute = Attribute {
        kind: 24,
        data: (&mut value as *mut BOOL).cast(),
        size: std::mem::size_of::<BOOL>(),
    };
    if !unsafe { get(hwnd, &mut attribute) }.as_bool() {
        return Err("PRESENCE_DDA_ATTRIBUTE_READ_FAILED".to_owned());
    }
    Ok(value.as_bool())
}

#[cfg(test)]
mod tests {
    use super::*;
    use windows_sys::Win32::UI::WindowsAndMessaging::{CreateWindowExW, DestroyWindow, WS_POPUP};

    struct HiddenWindow(isize);
    impl HiddenWindow {
        fn new() -> Self {
            let class: Vec<u16> = "STATIC\0".encode_utf16().collect();
            let hwnd = unsafe {
                CreateWindowExW(
                    0,
                    class.as_ptr(),
                    class.as_ptr(),
                    WS_POPUP,
                    0,
                    0,
                    8,
                    8,
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    std::ptr::null(),
                )
            };
            assert!(!hwnd.is_null());
            Self(hwnd as isize)
        }
    }
    impl Drop for HiddenWindow {
        fn drop(&mut self) {
            unsafe {
                DestroyWindow(self.0 as _);
            }
        }
    }

    #[test]
    fn overlapping_owners_restore_the_original_dda_attribute_only_on_last_drop() {
        let window = HiddenWindow::new();
        let hwnd = HWND(window.0 as *mut c_void);
        let baseline = previous_exclusion(hwnd).unwrap();
        let first = DdaExclusionLease::acquire(window.0).unwrap();
        let second = DdaExclusionLease::acquire(window.0).unwrap();
        assert!(previous_exclusion(hwnd).unwrap());
        drop(first);
        assert!(previous_exclusion(hwnd).unwrap());
        drop(second);
        assert_eq!(previous_exclusion(hwnd).unwrap(), baseline);
        assert_eq!(unsafe { GetPropW(window.0, PROPERTY.as_ptr()) }, 0);
    }

    #[test]
    fn old_generation_cannot_clear_replacement_window_ownership() {
        let window = HiddenWindow::new();
        let hwnd = HWND(window.0 as *mut c_void);
        let old = DdaExclusionLease::acquire(window.0).unwrap();
        // Simulate the observable reset of a destroyed/recreated HWND.
        unsafe {
            RemovePropW(window.0, PROPERTY.as_ptr());
        }
        set_window_excluded_from_dda(hwnd, false).unwrap();
        let current = DdaExclusionLease::acquire(window.0).unwrap();
        assert_ne!(old.token, current.token);
        drop(old);
        assert!(previous_exclusion(hwnd).unwrap());
        assert_eq!(
            unsafe { GetPropW(window.0, PROPERTY.as_ptr()) } as usize,
            current.token
        );
        drop(current);
        assert!(!previous_exclusion(hwnd).unwrap());
    }
}
