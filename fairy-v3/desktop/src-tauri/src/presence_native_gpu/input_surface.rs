use super::{register_native_surface_class, windows_stage_error, NATIVE_SURFACE_CLASS};
use windows::core::PCWSTR;
use windows::Win32::Foundation::{HINSTANCE, HWND};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DestroyWindow, SetWindowPos, HWND_BOTTOM, SWP_NOACTIVATE, SWP_NOOWNERZORDER,
    WS_CHILD, WS_DISABLED, WS_EX_NOACTIVATE, WS_EX_NOREDIRECTIONBITMAP, WS_EX_TRANSPARENT,
    WS_VISIBLE,
};

/// Paint-only child behind the existing WebView child. No separate input owner,
/// top-level window, message proxy or business state is introduced.
pub(super) struct InputCompositionSurface(pub HWND, (i32, i32));

impl InputCompositionSurface {
    pub fn new(parent: HWND, scale: f32) -> Result<Self, String> {
        register_native_surface_class()?;
        let module = unsafe { GetModuleHandleW(None) }
            .map_err(|error| windows_stage_error("INPUT_SURFACE_MODULE", error))?;
        let hwnd = unsafe {
            CreateWindowExW(
                WS_EX_NOACTIVATE | WS_EX_NOREDIRECTIONBITMAP | WS_EX_TRANSPARENT,
                NATIVE_SURFACE_CLASS,
                PCWSTR::null(),
                WS_CHILD | WS_DISABLED | WS_VISIBLE,
                0,
                0,
                1,
                1,
                Some(parent),
                None,
                Some(HINSTANCE(module.0)),
                None,
            )
        }
        .map_err(|error| windows_stage_error("INPUT_SURFACE_CREATE", error))?;
        let mut surface = Self(hwnd, (0, 0));
        surface.resize(scale)?;
        Ok(surface)
    }

    pub fn resize(&mut self, scale: f32) -> Result<(), String> {
        if !scale.is_finite() || !(0.25..=8.0).contains(&scale) {
            return Err("PRESENCE_NATIVE_GPU_INPUT_SURFACE_SCALE_INVALID".into());
        }
        let size = ((616.0 * scale).ceil() as i32, (360.0 * scale).ceil() as i32);
        if self.1 == size {
            return Ok(());
        }
        unsafe {
            SetWindowPos(
                self.0,
                Some(HWND_BOTTOM),
                0,
                0,
                size.0,
                size.1,
                SWP_NOACTIVATE | SWP_NOOWNERZORDER,
            )
        }
        .map_err(|error| windows_stage_error("INPUT_SURFACE_SIZE", error))?;
        self.1 = size;
        Ok(())
    }
}

impl Drop for InputCompositionSurface {
    fn drop(&mut self) {
        // Constructed, updated and dropped on the native compositor's owner thread.
        let _ = unsafe { DestroyWindow(self.0) };
    }
}
