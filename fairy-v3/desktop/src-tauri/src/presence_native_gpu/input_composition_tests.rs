use super::*;
use windows::Win32::UI::Input::KeyboardAndMouse::IsWindowEnabled;
use windows::Win32::UI::WindowsAndMessaging::GetParent;

struct HiddenInputWindow(HWND);
impl Drop for HiddenInputWindow {
    fn drop(&mut self) {
        let _ = unsafe { DestroyWindow(self.0) };
    }
}

struct WinRtTestApartment;
impl Drop for WinRtTestApartment {
    fn drop(&mut self) {
        unsafe { RoUninitialize() };
    }
}

#[test]
fn input_composition_releases_its_target_across_twenty_rebinds() {
    // Real hidden HWND/WinRT resource lifecycle, not visual or GPU-quality evidence.
    unsafe { RoInitialize(RO_INIT_MULTITHREADED) }.unwrap();
    let _apartment = WinRtTestApartment;
    let queue = unsafe {
        CreateDispatcherQueueController(DispatcherQueueOptions {
            dwSize: std::mem::size_of::<DispatcherQueueOptions>() as u32,
            threadType: DQTYPE_THREAD_CURRENT,
            apartmentType: DQTAT_COM_NONE,
        })
    }
    .unwrap();
    let compositor = Compositor::new().unwrap();
    let interop: ICompositorDesktopInterop = compositor.cast().unwrap();
    let surface_interop: ICompositorInterop = compositor.cast().unwrap();
    let window = HiddenInputWindow(
        unsafe {
            CreateWindowExW(
                WS_EX_NOREDIRECTIONBITMAP,
                w!("STATIC"),
                w!("Fairy input lifecycle test"),
                WS_POPUP,
                0,
                0,
                8,
                8,
                None,
                None,
                None,
                None,
            )
        }
        .unwrap(),
    );
    // WebView2 may already own the parent's below-child composition target.
    let occupied = unsafe { interop.CreateDesktopWindowTarget(window.0, false) }.unwrap();
    for index in 0..20 {
        let mut input = InputHostBackdropComposition::new(
            &compositor,
            &interop,
            &surface_interop,
            window.0,
            None,
            1.0,
            NativeGpuPresentation::default(),
        )
        .unwrap_or_else(|error| panic!("input rebind {index}: {error}"));
        let child = input.surface.0;
        assert_eq!(unsafe { GetParent(child) }.unwrap(), window.0);
        assert!(!unsafe { IsWindowEnabled(child) }.as_bool());
        input
            .update(
                [1.0, 1.25, 1.5, 2.0][index % 4],
                NativeGpuPresentation::default(),
            )
            .unwrap();
        drop(input);
        assert!(!unsafe { IsWindow(Some(child)) }.as_bool());
    }
    occupied.Close().unwrap();
    drop(surface_interop);
    drop(interop);
    compositor.Close().unwrap();
    let _ = queue.ShutdownQueueAsync();
}
