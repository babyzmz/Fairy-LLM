#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;

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
            backend::system_action_execute
        ])
        .setup(|app| {
            backend::initialize_backend(&app.handle());
            backend::start_desktop_action_bridge(&app.handle());
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
