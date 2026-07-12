use std::env;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use fairy_core_bridge::{CoreBridge, CoreBridgeError, CoreLaunchSpec};
use serde_json::{json, Value};
use tauri::{Manager, State, WebviewWindow};

use provider_configuration::{openrouter_profiles_json, ProviderConfigurationStore};
use provider_credentials::ProviderCredentialStore;

pub mod capture;
pub mod provider_configuration;
pub mod provider_credentials;

#[derive(Debug)]
pub struct WindowScopeError;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct AuxiliaryWindowPolicy {
    pub ignore_cursor_events: bool,
    pub focusable: bool,
}

pub fn authorize_core_rpc_window(label: &str) -> Result<(), WindowScopeError> {
    if label == "main" {
        Ok(())
    } else {
        Err(WindowScopeError)
    }
}

pub fn auxiliary_window_policy(label: &str) -> Option<AuxiliaryWindowPolicy> {
    match label {
        "pet" => Some(AuxiliaryWindowPolicy {
            ignore_cursor_events: false,
            focusable: true,
        }),
        "guide" => Some(AuxiliaryWindowPolicy {
            ignore_cursor_events: true,
            focusable: false,
        }),
        _ => None,
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
    core: Arc<Mutex<Option<CoreBridge>>>,
    data_dir: PathBuf,
    desktop_program: PathBuf,
    resource_dir: PathBuf,
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
    let response = match tauri::async_runtime::spawn_blocking(move || {
        let guard = core.lock().map_err(|_| CoreBridgeError::LockPoisoned)?;
        guard
            .as_ref()
            .ok_or(CoreBridgeError::WorkerInterrupted)?
            .call(request)
    })
    .await
    {
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

#[derive(serde::Deserialize)]
struct OpenRouterConfigureInput {
    api_key: String,
    model_id: String,
}

#[derive(serde::Serialize)]
struct OpenRouterStatus {
    configured: bool,
    model_id: Option<String>,
}

#[tauri::command]
async fn provider_openrouter_status(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<OpenRouterStatus, String> {
    authorize_core_rpc_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    let configuration = ProviderConfigurationStore::new(&state.data_dir)
        .load_openrouter()
        .map_err(|error| error.to_string())?;
    Ok(OpenRouterStatus {
        configured: ProviderCredentialStore::new(&state.data_dir).configured()
            && configuration.is_some(),
        model_id: configuration.map(|value| value.model_id),
    })
}

#[tauri::command]
async fn provider_openrouter_configure(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
    input: OpenRouterConfigureInput,
) -> Result<OpenRouterStatus, String> {
    authorize_core_rpc_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    let credentials = ProviderCredentialStore::new(&state.data_dir);
    let configurations = ProviderConfigurationStore::new(&state.data_dir);
    let configuration = configurations
        .save_openrouter(&input.model_id)
        .map_err(|error| error.to_string())?;
    credentials
        .save_openrouter(&input.api_key)
        .map_err(|error| error.to_string())?;
    restart_core(&state).map_err(|error| error.to_string())?;
    Ok(OpenRouterStatus {
        configured: true,
        model_id: Some(configuration.model_id),
    })
}

#[tauri::command]
async fn provider_openrouter_delete(
    window: WebviewWindow,
    state: State<'_, DesktopState>,
) -> Result<OpenRouterStatus, String> {
    authorize_core_rpc_window(window.label()).map_err(|_| "Window is not authorized".to_owned())?;
    ProviderCredentialStore::new(&state.data_dir)
        .delete_openrouter()
        .map_err(|error| error.to_string())?;
    ProviderConfigurationStore::new(&state.data_dir)
        .delete_openrouter()
        .map_err(|error| error.to_string())?;
    restart_core(&state).map_err(|error| error.to_string())?;
    Ok(OpenRouterStatus {
        configured: false,
        model_id: None,
    })
}

fn development_core_root() -> PathBuf {
    env::var_os("FAIRY_CORE_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../core"))
}

pub fn bundled_core_path(desktop_executable: &std::path::Path) -> PathBuf {
    desktop_executable
        .parent()
        .unwrap_or_else(|| std::path::Path::new("."))
        .join(if cfg!(windows) {
            "fairy-core.exe"
        } else {
            "fairy-core"
        })
}

pub fn bundled_git_path(resource_dir: &std::path::Path) -> PathBuf {
    resource_dir.join("runtime/git/cmd/git.exe")
}

fn core_launch_spec(data_dir: &std::path::Path) -> Result<CoreLaunchSpec, std::io::Error> {
    if cfg!(debug_assertions) {
        return Ok(CoreLaunchSpec::development(
            development_core_root(),
            data_dir,
        ));
    }
    let executable = std::env::current_exe()?;
    let core_program = env::var_os("FAIRY_CORE_PROGRAM")
        .map(PathBuf::from)
        .unwrap_or_else(|| bundled_core_path(&executable));
    Ok(CoreLaunchSpec::bundled(core_program, data_dir))
}

fn configured_core_launch(
    data_dir: &Path,
    desktop_program: &Path,
    resource_dir: &Path,
) -> Result<CoreLaunchSpec, Box<dyn std::error::Error>> {
    let mut launch = core_launch_spec(data_dir)?;
    launch.env.insert(
        "FAIRY_LOCAL_WORKER_PROGRAM".to_owned(),
        desktop_program.to_string_lossy().into_owned(),
    );
    launch.env.insert(
        "FAIRY_LOCAL_WORKER_ARGS_JSON".to_owned(),
        "[\"--local-worker\"]".to_owned(),
    );
    if !cfg!(debug_assertions) {
        launch.env.insert(
            "FAIRY_GIT_PROGRAM".to_owned(),
            bundled_git_path(resource_dir)
                .to_string_lossy()
                .into_owned(),
        );
    }
    let configuration = ProviderConfigurationStore::new(data_dir).load_openrouter()?;
    let secret = ProviderCredentialStore::new(data_dir).load_openrouter()?;
    if let (Some(configuration), Some(secret)) = (configuration, secret) {
        launch.env.insert(
            "FAIRY_PROVIDER_PROFILES_JSON".to_owned(),
            openrouter_profiles_json(&configuration),
        );
        launch.env.insert(
            "FAIRY_PROVIDER_SECRET_REFS_JSON".to_owned(),
            "{\"openrouter\":\"FAIRY_PROVIDER_SECRET_OPENROUTER\"}".to_owned(),
        );
        launch
            .env
            .insert("FAIRY_PROVIDER_SECRET_OPENROUTER".to_owned(), secret);
    }
    Ok(launch)
}

fn restart_core(state: &DesktopState) -> Result<(), Box<dyn std::error::Error>> {
    let mut core = state
        .core
        .lock()
        .map_err(|_| CoreBridgeError::LockPoisoned)?;
    *core = None;
    let launch =
        configured_core_launch(&state.data_dir, &state.desktop_program, &state.resource_dir)?;
    *core = Some(CoreBridge::spawn_verified(launch)?);
    Ok(())
}

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let data_dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&data_dir)?;
            let desktop_program = std::env::current_exe()?;
            let resource_dir = app.path().resource_dir()?;
            let launch = configured_core_launch(&data_dir, &desktop_program, &resource_dir)
                .map_err(|error| std::io::Error::other(error.to_string()))?;
            let bridge = CoreBridge::spawn_verified(launch)?;
            app.manage(DesktopState {
                core: Arc::new(Mutex::new(Some(bridge))),
                data_dir,
                desktop_program,
                resource_dir,
            });
            for label in ["pet", "guide"] {
                let Some(policy) = auxiliary_window_policy(label) else {
                    continue;
                };
                let Some(window) = app.get_webview_window(label) else {
                    continue;
                };
                window.set_ignore_cursor_events(policy.ignore_cursor_events)?;
                window.set_focusable(policy.focusable)?;
                if label == "guide" {
                    window.show()?;
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            core_rpc,
            provider_openrouter_status,
            provider_openrouter_configure,
            provider_openrouter_delete,
            capture::list_capture_surfaces,
            capture::capture_surface
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Fairy desktop");
}
