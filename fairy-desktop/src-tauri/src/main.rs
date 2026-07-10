#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;

use tauri::{Manager, PhysicalPosition};

const PET_WINDOW_LABEL: &str = "fairy-pet";
const PET_WINDOW_WIDTH: i32 = 296;
const PET_WINDOW_HEIGHT: i32 = 420;
const PET_MARGIN_X: i32 = 24;
const PET_MARGIN_Y: i32 = 80;

fn place_pet_window(app: &tauri::AppHandle) {
    let Some(window) = app.get_webview_window(PET_WINDOW_LABEL) else {
        return;
    };
    let _ = window.set_always_on_top(true);
    let monitor = window
        .current_monitor()
        .ok()
        .flatten()
        .or_else(|| window.primary_monitor().ok().flatten());
    let Some(monitor) = monitor else {
        return;
    };
    let monitor_position = monitor.position();
    let monitor_size = monitor.size();
    let x = monitor_position.x + monitor_size.width as i32 - PET_WINDOW_WIDTH - PET_MARGIN_X;
    let y = monitor_position.y + monitor_size.height as i32 - PET_WINDOW_HEIGHT - PET_MARGIN_Y;
    let clamped_x = x.max(monitor_position.x);
    let clamped_y = y.max(monitor_position.y);
    let _ = window.set_position(PhysicalPosition::new(clamped_x, clamped_y));
}

fn main() {
    let mut context = tauri::generate_context!();
    let headless_e2e = std::env::var("FAIRY_HEADLESS_E2E")
        .map(|value| matches!(value.trim(), "1" | "true" | "TRUE" | "True"))
        .unwrap_or(false);
    if headless_e2e {
        context.config_mut().app.windows.clear();
    }
    tauri::Builder::default()
        .manage(backend::BackendProcess::default())
        .invoke_handler(tauri::generate_handler![
            backend::system_state,
            backend::backend_control,
            backend::system_action_execute,
            backend::persist_chat_attachment,
            backend::quit_app
        ])
        .setup(|app| {
            backend::initialize_backend(&app.handle());
            backend::start_desktop_action_bridge(&app.handle());
            place_pet_window(&app.handle());
            if let Ok(value) = std::env::var("FAIRY_AUTO_EXIT_AFTER_MS") {
                if let Ok(delay_ms) = value.trim().parse::<u64>() {
                    if delay_ms > 0 {
                        let app_handle = app.handle().clone();
                        std::thread::spawn(move || {
                            std::thread::sleep(std::time::Duration::from_millis(delay_ms));
                            app_handle.exit(0);
                        });
                    }
                }
            }
            Ok(())
        })
        .build(context)
        .expect("error while building Fairy desktop shell")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                backend::shutdown_backend(app);
            }
        });
}
