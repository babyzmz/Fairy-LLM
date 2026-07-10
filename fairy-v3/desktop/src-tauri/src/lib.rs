use std::env;
use std::path::PathBuf;
use std::sync::Arc;

use fairy_core_bridge::{CoreBridge, CoreBridgeError, CoreLaunchSpec};
use serde_json::{json, Value};
use tauri::{Manager, State, WebviewWindow};

#[derive(Debug)]
pub struct WindowScopeError;

pub fn authorize_core_rpc_window(label: &str) -> Result<(), WindowScopeError> {
    if label == "main" {
        Ok(())
    } else {
        Err(WindowScopeError)
    }
}

pub fn bridge_failure_response(id: Value, error: &CoreBridgeError) -> Value {
    let error_code = if matches!(error, CoreBridgeError::WorkerInterrupted) {
        "WORKER_INTERRUPTED"
    } else {
        "CORE_PROTOCOL_ERROR"
    };
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": {
            "code": -32050,
            "message": error.to_string(),
            "data": { "error_code": error_code }
        }
    })
}

struct DesktopState {
    core: Arc<CoreBridge>,
}

#[tauri::command]
async fn core_rpc(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    request: Value,
) -> Result<Value, String> {
    let request_id = request.get("id").cloned().unwrap_or(Value::Null);
    if authorize_core_rpc_window(window.label()).is_err() {
        return Ok(json!({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32001,
                "message": "Window is not authorized to access Fairy Core",
                "data": { "error_code": "SCOPE_MISMATCH" }
            }
        }));
    }

    let core = Arc::clone(&state.core);
    let response = match tauri::async_runtime::spawn_blocking(move || core.call(request)).await {
        Ok(Ok(response)) => response,
        Ok(Err(error)) => bridge_failure_response(request_id, &error),
        Err(error) => json!({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32050,
                "message": error.to_string(),
                "data": { "error_code": "WORKER_INTERRUPTED" }
            }
        }),
    };
    Ok(response)
}

fn development_core_root() -> PathBuf {
    env::var_os("FAIRY_CORE_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../core"))
}

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let data_dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&data_dir)?;
            let mut launch = CoreLaunchSpec::development(development_core_root(), data_dir);
            launch.env.insert(
                "FAIRY_LOCAL_WORKER_PROGRAM".to_owned(),
                std::env::current_exe()?.to_string_lossy().into_owned(),
            );
            launch.env.insert(
                "FAIRY_LOCAL_WORKER_ARGS_JSON".to_owned(),
                "[\"--local-worker\"]".to_owned(),
            );
            let bridge = CoreBridge::spawn(launch)?;
            app.manage(DesktopState {
                core: Arc::new(bridge),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![core_rpc])
        .run(tauri::generate_context!())
        .expect("failed to run Fairy desktop");
}
